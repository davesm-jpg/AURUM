import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from typing import Protocol
from urllib.request import Request

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.feed_frames import FeedFrame
from aurum.instruments import MgcContract
from aurum.market_data import Candle
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import (
    HeartbeatStatus,
    MarketMonitor,
    MonitorConfig,
    MonitorState,
    describe_monitor,
    market_events_page,
    market_evidence_started_at,
    monitor_events,
    operational_monitor_status,
)
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, SetupEvent, StrategyReplay
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


def test_operational_read_fails_closed_after_heartbeat_deadline(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    monitor = fixture_monitor(store, calendar((0, 10)))
    unobserved = describe_monitor(store, monitor.monitor_id)
    assert operational_monitor_status(unobserved, START)["heartbeat_status"] == (
        HeartbeatStatus.UNOBSERVED
    )
    monitor.set_connection(True, START)
    ready = monitor.ingest(frame(0))
    assert ready["state"] == MonitorState.READY
    close = START + MINUTE
    deadline = close + timedelta(seconds=90)
    current = operational_monitor_status(ready, deadline)
    assert current["heartbeat_status"] == HeartbeatStatus.CURRENT
    assert current["alerts_suspended"] is False
    overdue = operational_monitor_status(ready, deadline + timedelta(microseconds=1))
    assert overdue["heartbeat_status"] == HeartbeatStatus.OVERDUE
    assert overdue["alerts_suspended"] is True
    assert overdue["state"] == MonitorState.READY
    future = operational_monitor_status(ready, close - timedelta(microseconds=1))
    assert future["heartbeat_status"] == HeartbeatStatus.CLOCK_AHEAD
    assert future["alerts_suspended"] is True
    assert describe_monitor(store, monitor.monitor_id) == ready

    replay = fixture_monitor(store, calendar((0, 10)), FeedStatus.SIMULATED)
    simulated = operational_monitor_status(describe_monitor(store, replay.monitor_id), deadline)
    assert simulated["heartbeat_status"] == HeartbeatStatus.NOT_APPLICABLE
    assert simulated["alerts_suspended"] is True

    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    with http.TestClient(api.create_app(store)) as client:
        prefix = f"/api/monitors/{monitor.monitor_id}"
        for route in (prefix, f"{prefix}/chart"):
            response = client.get(route)
            assert response.status_code == 200
            payload = response.json()
            assert payload["heartbeat_status"] == "overdue"
            assert payload["alerts_suspended"] is True
    assert describe_monitor(store, monitor.monitor_id) == ready


@pytest.mark.parametrize(
    ("error_code", "succeeds", "expected_delays"),
    [
        (sqlite3.SQLITE_BUSY | (3 << 8), True, [0.25]),
        (sqlite3.SQLITE_LOCKED, False, [0.25, 0.5]),
        (sqlite3.SQLITE_IOERR, False, []),
    ],
)
def test_stream_store_retry_preserves_receipt_and_atomic_frame_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_code: int,
    succeeds: bool,
    expected_delays: list[float],
) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    monitor = fixture_monitor(store, calendar((0, 8)))
    source = WebSocketSource("wss://synthetic.invalid", FeedStatus.REAL_TIME)
    current = START
    queue = iter((frame(0), frame(1)))
    delays = []

    class FixtureConnection:
        def recv(self, timeout: float | None = None) -> str:
            nonlocal current
            value = next(queue)
            current = value.received_at
            payload = value.candle.as_dict()
            del payload["available_at"]
            return json.dumps({"event": "candle_closed", **payload})

    @contextmanager
    def connect(_: WebSocketSource) -> Iterator[FixtureConnection]:
        yield FixtureConnection()

    def sleep(seconds: float) -> None:
        nonlocal current
        delays.append(seconds)
        current += timedelta(seconds=seconds)

    original = MarketMonitor._ingest
    invocations = 0
    error = sqlite3.OperationalError("synthetic SQLite failure after applying frame")
    error.sqlite_errorcode = error_code

    def ingest(
        instance: MarketMonitor,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        value: FeedFrame,
        now: datetime,
        storage_retry: bool,
    ) -> int:
        nonlocal invocations
        invocations += 1
        result = original(instance, db, row, value, now, storage_retry)
        if invocations == 1 or not succeeds:
            raise error
        return result

    monkeypatch.setattr(WebSocketSource, "connect", connect)
    monkeypatch.setattr(MarketMonitor, "_ingest", ingest)
    if succeeds:
        state = run_stream(monitor, source, max_frames=2, clock=lambda: current, sleeper=sleep)
        assert state["last_applied_open"] == (START + MINUTE).isoformat()
    else:
        with pytest.raises(sqlite3.OperationalError) as failure:
            run_stream(monitor, source, max_frames=2, clock=lambda: current, sleeper=sleep)
        assert failure.value is error
        assert invocations == len(expected_delays) + 1
    assert delays == expected_delays
    assert describe_monitor(store, monitor.monitor_id)["connected"] is False
    with store.connection() as db:
        applied = [
            tuple(row)
            for row in db.execute(
                """SELECT received_at, applied_at, execution_eligible
                   FROM monitor_minutes ORDER BY opened_at"""
            )
        ]
    if succeeds:
        first, second = START + MINUTE, START + 2 * MINUTE
        assert applied == [
            (first.isoformat(), (first + timedelta(seconds=0.25)).isoformat(), 0),
            (second.isoformat(), second.isoformat(), 1),
        ]
        (recovery,) = [
            event
            for event in monitor_events(store, monitor.monitor_id)
            if event["event"] == "feed_storage_retry_recovered"
        ]
        assert recovery["source_received_at"] == first.isoformat()
        assert recovery["at"] == (first + timedelta(seconds=0.25)).isoformat()
    else:
        assert applied == []


def test_application_clock_after_receipt_controls_live_eligibility(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    monitor = fixture_monitor(store, calendar((0, 8)))
    monitor.set_connection(True, START)
    value = frame(0)
    applied = value.received_at + timedelta(seconds=91)
    health = monitor.ingest(value, application_clock=lambda: applied)
    assert health["last_received"] == value.received_at.isoformat()
    assert health["checked_at"] == applied.isoformat()
    with store.connection() as db:
        row = db.execute("SELECT received_at, applied_at, execution_eligible FROM monitor_minutes")
        assert tuple(row.fetchone()) == (value.received_at.isoformat(), applied.isoformat(), 0)


def test_invalid_stream_frame_latches_fault_and_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    monitor = fixture_monitor(store, calendar((0, 8)), FeedStatus.SIMULATED)
    source = WebSocketSource("ws://127.0.0.1:18991/synthetic", FeedStatus.SIMULATED)

    class FixtureConnection:
        def recv(self, timeout: float | None = None) -> str:
            return '{"event":"candle_closed"}'

    @contextmanager
    def connect(_: WebSocketSource) -> Iterator[FixtureConnection]:
        yield FixtureConnection()

    monkeypatch.setattr(WebSocketSource, "connect", connect)
    state = run_stream(monitor, source, max_frames=1, clock=lambda: START)
    assert state["state"] == MonitorState.INVALID
    assert state["fault"] == "invalid_normalized_frame"
    assert state["connected"] is False
    assert state["last_applied_open"] is None
    assert [event["event"] for event in monitor_events(store, monitor.monitor_id)].count(
        "feed_disconnected"
    ) == 1


def test_monitor_restart_rebuilds_without_blocking_ingestion_and_catches_up_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    schedule = calendar((0, 10))
    active = fixture_monitor(store, schedule, FeedStatus.SIMULATED)
    active.set_connection(True, START)
    for index in range(5):
        active.ingest(frame(index, mode=FeedStatus.SIMULATED))

    rebuilding, resume = Event(), Event()
    restored = []
    original = StrategyReplay.push

    def pause(self: StrategyReplay, candle: Candle) -> tuple[SetupEvent, ...]:
        restored.append(candle.opened_at)
        if len(restored) == 1:
            rebuilding.set()
            assert resume.wait(8), "synthetic restart was not released"
        return original(self, candle)

    monkeypatch.setattr(StrategyReplay, "push", pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fixture_monitor, store, schedule, FeedStatus.SIMULATED)
        try:
            assert rebuilding.wait(5), "synthetic restart did not reach replay"
            assert (
                active.ingest(frame(5, mode=FeedStatus.SIMULATED))["last_applied_open"]
                == (START + 5 * MINUTE).isoformat()
            )
        finally:
            resume.set()
        restarted = future.result(timeout=5)

    restarted.ingest(frame(6, mode=FeedStatus.SIMULATED))
    active.ingest(frame(7, mode=FeedStatus.SIMULATED))
    assert restored == [START + index * MINUTE for index in range(7)]
    contexts = market_events_page(store, active.monitor_id, kind="market_context_observed")
    assert [item["confirmed_at"] for item in contexts] == [(START + 5 * MINUTE).isoformat()]


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


def test_batched_synthetic_ingestion_matches_single_frames_and_rolls_back_atomically(
    tmp_path: Path,
) -> None:
    first = DatasetStore(tmp_path / "synthetic-single.sqlite")
    second = DatasetStore(tmp_path / "synthetic-batch.sqlite")
    schedule = calendar((0, 12))
    single = fixture_monitor(first, schedule, FeedStatus.SIMULATED)
    batched = fixture_monitor(second, schedule, FeedStatus.SIMULATED)
    single.set_connection(True, START)
    batched.set_connection(True, START)
    frames = tuple(
        frame(index, received=max(index + 1, 3) if index else 1, mode=FeedStatus.SIMULATED)
        for index in (0, 2, 1, *range(3, 12))
    )
    with pytest.raises(ValueError, match="individually"):
        batched.ingest_replay_batch((frame(0),))
    with sqlite3.connect(second.path) as db:
        db.execute(
            """CREATE TRIGGER fail_batch BEFORE UPDATE ON monitor_minutes
               WHEN new.opened_at = '2025-03-10T13:04:00+00:00'
               BEGIN SELECT RAISE(ABORT, 'synthetic batch failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic batch failure"):
        batched.ingest_replay_batch(frames)
    with sqlite3.connect(second.path) as db:
        assert db.execute("SELECT count(*) FROM monitor_minutes").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM monitor_market_events").fetchone() == (0,)
        db.execute("DROP TRIGGER fail_batch")
    for minute in frames:
        expected = single.ingest(minute)
    assert batched.ingest_replay_batch(frames) == expected
    reference = single.monitor_id
    assert list(monitor_events(first, reference)) == list(monitor_events(second, reference))
    assert market_events_page(first, reference) == market_events_page(second, reference)
    with sqlite3.connect(first.path) as left, sqlite3.connect(second.path) as right:
        query = "SELECT * FROM monitor_minutes ORDER BY opened_at"
        assert left.execute(query).fetchall() == right.execute(query).fetchall()


def test_monitor_evidence_migration_keeps_legacy_identity_and_unknown_observation_times(
    tmp_path: Path,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-v5.sqlite")
    schedule = calendar((0, 10))
    original = fixture_monitor(store, schedule, FeedStatus.SIMULATED)
    for index in range(5):
        original.ingest(frame(index, mode=FeedStatus.SIMULATED))
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE book_quotes")
        db.execute("DROP TABLE quote_series")
        db.execute("DROP TABLE trade_prints")
        db.execute("DROP TABLE monitor_market_events")
        db.execute("DROP TABLE forward_report_publications")
        db.execute("DROP TABLE runtime_profiles")
        db.execute("DROP TABLE forward_events")
        db.execute("DROP TABLE forward_runs")
        db.execute("DROP INDEX monitor_signal_close")
        db.execute("DROP INDEX monitor_setup_observation")
        db.execute("DROP INDEX monitor_observed_start")
        db.execute("DROP INDEX monitor_minute_observation")
        db.execute("DROP INDEX structure_event_available")
        db.execute("ALTER TABLE monitor_minutes DROP COLUMN execution_eligible")
        db.execute("ALTER TABLE monitor_minutes DROP COLUMN applied_at")
        db.execute("DROP TABLE economic_captures")
        db.execute("DROP TABLE monitor_economic_updates")
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
