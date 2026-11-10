import json
import sqlite3
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from threading import Thread
from typing import Protocol
from urllib.request import Request

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.feed_frames import FeedFrame
from aurum.instruments import MgcContract
from aurum.market_data import Candle
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import (
    MarketMonitor,
    MonitorConfig,
    MonitorState,
    market_events_page,
    market_evidence_started_at,
    monitor_events,
)
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
from aurum.streaming import HttpsGapBackfill, WebSocketSource, run_stream
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-03-10T13:00Z")
CONTRACT = MgcContract("MGC-2025-04")


def calendar(*windows: tuple[int, int]) -> SessionCalendar:
    spans = tuple(
        TradingWindow(START + low * MINUTE, START + high * MINUTE) for low, high in windows
    )
    return SessionCalendar(
        "synthetic monitor calendar",
        "fixture-v1",
        TradingWindow(spans[0].start, spans[-1].end),
        sessions=(TradingSession(date(2025, 3, 10), spans),),
    )


def fixture_monitor(
    store: DatasetStore,
    schedule: SessionCalendar,
    mode: FeedStatus = FeedStatus.REAL_TIME,
) -> MarketMonitor:
    return MarketMonitor(
        store,
        schedule,
        CONTRACT,
        MonitorConfig("synthetic fixture transport", mode),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
    )


def frame(
    minute: int,
    *,
    received: int | None = None,
    mode: FeedStatus = FeedStatus.REAL_TIME,
    price: int = 29000,
) -> FeedFrame:
    candle = Candle(CONTRACT, START + minute * MINUTE, price, price, price, price, 2)
    return FeedFrame.from_replay(
        candle,
        mode,
        START + (minute + 1 if received is None else received) * MINUTE,
    )


def test_pending_gap_and_restart_recover_durable_prefix(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "monitor.sqlite")
    schedule = calendar((0, 8))
    monitor = fixture_monitor(store, schedule)
    assert monitor.set_connection(True, START)["state"] == MonitorState.WARMING
    assert monitor.ingest(frame(0))["state"] == MonitorState.READY
    future = monitor.ingest(frame(2))
    assert future["state"] == MonitorState.RECOVERING
    assert future["pending_minutes"] == 1
    assert future["alerts_suspended"] is True
    assert monitor.gap() == TradingWindow(START + MINUTE, START + 2 * MINUTE)

    restarted = fixture_monitor(store, schedule)
    assert restarted.monitor_id == monitor.monitor_id
    filled = restarted.ingest(frame(1, received=3, mode=FeedStatus.HISTORICAL))
    assert filled["state"] == MonitorState.READY
    assert filled["last_applied_open"] == (START + 2 * MINUTE).isoformat()
    assert filled["pending_minutes"] == 0
    assert restarted.ingest(frame(1, received=4, mode=FeedStatus.HISTORICAL))["fault"] is None
    bad = restarted.ingest(frame(1, received=4, price=29001))
    assert bad["fault"] == "conflicting_duplicate"
    assert bad["alerts_suspended"] is True
    assert (
        restarted.ingest(frame(3, received=4))["last_applied_open"] == filled["last_applied_open"]
    )


def test_market_closure_disconnection_staleness_and_unknown_coverage(tmp_path: Path) -> None:
    monitor = fixture_monitor(DatasetStore(tmp_path / "monitor.sqlite"), calendar((0, 2), (60, 70)))
    monitor.set_connection(True, START)
    monitor.ingest(frame(0))
    monitor.ingest(frame(1))
    assert monitor.refresh(START + 30 * MINUTE)["state"] == MonitorState.CLOSED
    assert monitor.set_connection(False, START + 60 * MINUTE)["state"] == MonitorState.DISCONNECTED
    monitor.set_connection(True, START + 60 * MINUTE)
    assert monitor.refresh(START + 63 * MINUTE)["state"] == MonitorState.STALE
    assert monitor.refresh(START + 70 * MINUTE)["state"] == MonitorState.UNAVAILABLE


def test_monitor_transaction_rollback_can_retry_a_failed_apply(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "monitor.sqlite")
    monitor = fixture_monitor(store, calendar((0, 8)))
    monitor.set_connection(True, START)
    with sqlite3.connect(store.path) as db:
        db.execute(
            """CREATE TRIGGER fail_monitor BEFORE UPDATE ON monitor_minutes
               BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        monitor.ingest(frame(0))
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM monitor_minutes").fetchone() == (0,)
        db.execute("DROP TRIGGER fail_monitor")
    assert monitor.ingest(frame(0))["last_applied_open"] == START.isoformat()


def test_monitor_evidence_migration_keeps_legacy_identity_and_unknown_observation_times(
    tmp_path: Path,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-v5.sqlite")
    schedule = calendar((0, 10))
    original = fixture_monitor(store, schedule, FeedStatus.SIMULATED)
    for index in range(5):
        original.ingest(frame(index, mode=FeedStatus.SIMULATED))
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE monitor_market_events")
        db.execute("DROP TABLE forward_report_publications")
        db.execute("DROP TABLE runtime_profiles")
        db.execute("DROP TABLE forward_events")
        db.execute("DROP TABLE forward_runs")
        db.execute("DROP INDEX monitor_signal_close")
        db.execute("DROP INDEX monitor_setup_observation")
        db.execute("DROP INDEX monitor_observed_start")
        db.execute("DROP INDEX monitor_minute_observation")
        db.execute("ALTER TABLE monitor_minutes DROP COLUMN execution_eligible")
        db.execute("ALTER TABLE monitor_minutes DROP COLUMN applied_at")
        db.execute("PRAGMA user_version = 5")
    assert market_events_page(store, original.monitor_id) == []

    migrated = fixture_monitor(store, schedule, FeedStatus.SIMULATED)
    assert migrated.monitor_id == original.monitor_id
    assert (
        market_evidence_started_at(store, migrated.monitor_id, schedule.coverage.end.isoformat())
        is None
    )
    for index in range(5, 10):
        migrated.ingest(frame(index, mode=FeedStatus.SIMULATED))
    (context,) = market_events_page(store, migrated.monitor_id, kind="market_context_observed")
    assert context["confirmed_at"] == (START + 10 * MINUTE).isoformat()
    assert context["observed_at"] == context["confirmed_at"]
    assert (
        market_evidence_started_at(store, migrated.monitor_id, schedule.coverage.end.isoformat())
        == (START + 6 * MINUTE).isoformat()
    )
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT count(*) FROM monitor_minutes WHERE applied_at IS NULL"
        ).fetchone() == (5,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_market_evidence_failure_rolls_back_the_applied_minute(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    monitor = fixture_monitor(store, calendar((0, 10)), FeedStatus.SIMULATED)
    for index in range(4):
        monitor.ingest(frame(index, mode=FeedStatus.SIMULATED))
    with sqlite3.connect(store.path) as db:
        db.execute(
            """CREATE TRIGGER fail_evidence BEFORE INSERT ON monitor_market_events
               BEGIN SELECT RAISE(ABORT, 'synthetic market evidence failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic market evidence failure"):
        monitor.ingest(frame(4, mode=FeedStatus.SIMULATED))
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM monitor_minutes").fetchone() == (4,)
        db.execute("DROP TRIGGER fail_evidence")
    result = monitor.ingest(frame(4, mode=FeedStatus.SIMULATED))
    assert result["last_applied_open"] == (START + 4 * MINUTE).isoformat()
    (context,) = market_events_page(store, monitor.monitor_id, kind="market_context_observed")
    assert context["observed_at"] == (START + 5 * MINUTE).isoformat()


def test_normalized_https_backfill_preserves_source_and_drains_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "monitor.sqlite")
    monitor = fixture_monitor(store, calendar((0, 8)))
    monitor.set_connection(True, START)
    monitor.ingest(frame(0))
    monitor.ingest(frame(2))

    class SyntheticOpener:
        def open(self, request: Request, *, timeout: int) -> BytesIO:
            assert "contract=MGC-2025-04" in request.full_url and timeout == 30
            return BytesIO(
                b"timestamp,contract,open,high,low,close,volume\n"
                b"2025-03-10T13:01Z,MGC-2025-04,2900,2900,2900,2900,2\n"
            )

    monkeypatch.setattr("aurum.providers.build_opener", lambda _: SyntheticOpener())
    gap = monitor.gap()
    assert gap is not None
    HttpsGapBackfill("https://data.invalid/ohlcv").recover(monitor, gap, lambda: START + 3 * MINUTE)
    assert monitor.gap() is None
    (recovered,) = [
        event
        for event in monitor_events(store, monitor.monitor_id)
        if event["event"] == "feed_backfill_completed"
    ]
    assert (
        store.describe(str(recovered["reason"]))["provenance"]["source_origin"]
        == "https://data.invalid"
    )
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT source_status, applied FROM monitor_minutes ORDER BY opened_at"
        ).fetchall() == [("real_time", 1), ("historical", 1), ("real_time", 1)]


def test_local_websocket_transports_only_explicit_synthetic_data(tmp_path: Path) -> None:
    server_module = pytest.importorskip("websockets.sync.server")
    now = datetime.now(UTC)
    start = now.replace(second=0, microsecond=0) - MINUTE
    span = TradingWindow(start, start + 4 * MINUTE)
    schedule = SessionCalendar(
        "synthetic local WebSocket calendar",
        "fixture-v1",
        span,
        sessions=(TradingSession(start.date(), (span,)),),
    )
    store = DatasetStore(tmp_path / "monitor.sqlite")
    monitor = fixture_monitor(store, schedule, FeedStatus.SIMULATED)
    raw = {
        "event": "candle_closed",
        "contract": CONTRACT.code,
        "opened_at": start.isoformat(),
        "open_ticks": 29000,
        "high_ticks": 29001,
        "low_ticks": 28999,
        "close_ticks": 29000,
        "volume": 2,
        "source_status": "simulated",
    }

    class SyntheticConnection(Protocol):
        def send(self, data: str) -> None: ...

    def handler(connection: SyntheticConnection) -> None:
        connection.send(json.dumps(raw))

    with server_module.serve(handler, "127.0.0.1", 0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.socket.getsockname()[1]
            source = WebSocketSource(f"ws://127.0.0.1:{port}", FeedStatus.SIMULATED)
            state = run_stream(monitor, source, max_frames=1)
            assert state["last_applied_open"] == start.isoformat()
            assert state["alerts_suspended"] is True
            assert state["connected"] is False
        finally:
            server.shutdown()
            thread.join(timeout=5)
    with pytest.raises(ValueError, match="must be WSS"):
        WebSocketSource("ws://127.0.0.1:1", FeedStatus.REAL_TIME)
    with pytest.raises(ValueError, match="disagrees"):
        FeedFrame.from_message(json.dumps(raw), FeedStatus.REAL_TIME, now)
