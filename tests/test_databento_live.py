"""Synthetic native SDK records only. These tests never open a vendor connection."""

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sys import executable
from typing import Any

import pytest
from test_notifications import AT, START, fixture_monitor

from aurum.databento_live import DatabentoConnection, DatabentoSource
from aurum.instruments import MgcContract
from aurum.monitoring import MonitorState
from aurum.providers import FeedStatus
from aurum.streaming import FeedDisconnected, run_stream
from aurum.timestamps import MINUTE

db = pytest.importorskip("databento")
CONTRACT = MgcContract("MGC-2025-04")
NOW = AT + 2 * MINUTE


def nanos(at: datetime) -> int:
    return (at - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000


def mapping(**changes: Any) -> Any:
    values = {
        "publisher_id": 0,
        "instrument_id": 7,
        "ts_event": nanos(START),
        "stype_in": db.SType.RAW_SYMBOL,
        "stype_in_symbol": "MGCJ5",
        "stype_out": db.SType.RAW_SYMBOL,
        "stype_out_symbol": "MGCJ5",
        "start_ts": nanos(START),
        "end_ts": 2**64 - 1,
    }
    return db.SymbolMappingMsg(**(values | changes))


def bar(opened: datetime = AT, **changes: Any) -> Any:
    values = {
        "rtype": db.RType.OHLCV_1M,
        "publisher_id": 1,
        "instrument_id": 7,
        "ts_event": nanos(opened),
        "open": 2_900_000_000_000,
        "high": 2_900_200_000_000,
        "low": 2_899_900_000_000,
        "close": 2_900_100_000_000,
        "volume": 2,
    }
    return db.OHLCVMsg(**(values | changes))


class SyntheticClient:
    def __init__(self, records: tuple[Any, ...] = ()) -> None:
        self.records = records
        self.connected = True
        self.terminated = False
        self.subscription: dict[str, Any] = {}
        self.record_callback: Any = None

    def add_callback(self, callback: Any, error_callback: Any) -> None:
        self.record_callback = callback

    def subscribe(self, **options: Any) -> None:
        self.subscription = options

    def start(self) -> None:
        for record in self.records:
            self.record_callback(record)

    def terminate(self) -> None:
        self.terminated = True
        self.connected = False

    def block_for_close(self, *, timeout: int) -> None:
        assert timeout == 5

    def is_connected(self) -> bool:
        return self.connected


def source(**options: Any) -> DatabentoSource:
    return DatabentoSource(CONTRACT, "MGCJ5", clock=lambda: NOW, **options)


def test_native_records_keep_exact_mapping_prices_clock_and_replay_classification() -> None:
    connection = DatabentoConnection(source(start=START), SyntheticClient(), db, replay_start=START)
    proof = mapping()
    assert connection.decode(proof, NOW) is None
    record = bar()
    frame = connection.decode(record, NOW)
    assert frame is not None
    assert frame.candle.opened_at == AT and frame.received_at == NOW
    assert frame.candle.close_ticks == 29001
    assert frame.source_status is FeedStatus.HISTORICAL
    raw = json.loads(frame.raw)
    assert raw["ohlcv_dbn_hex"] == bytes(record).hex()
    assert raw["mapping_dbn_hex"] == bytes(proof).hex()
    assert raw["adapter"]["contract"] == CONTRACT.code
    connection.decode(
        db.SystemMsg(nanos(NOW), "synthetic caught up", db.SystemCode.REPLAY_COMPLETED), NOW
    )
    fresh = connection.decode(bar(AT + MINUTE), NOW)
    assert fresh is not None and fresh.source_status is FeedStatus.REAL_TIME


@pytest.mark.parametrize(
    "case", ("mapping", "symbol", "interval", "price", "rtype", "early", "publisher")
)
def test_native_invalid_records_fail_closed(case: str) -> None:
    connection = DatabentoConnection(source(), SyntheticClient(), db)
    if case != "mapping":
        proof = (
            mapping(stype_out_symbol="GCJ5")
            if case == "symbol"
            else mapping(end_ts=nanos(AT) if case == "interval" else 2**64 - 1)
        )
        if case == "symbol":
            with pytest.raises(ValueError, match="pinned contract"):
                connection.decode(proof, NOW)
            return
        connection.decode(proof, NOW)
    record = (
        bar(open=2_900_000_000_001)
        if case == "price"
        else bar(rtype=db.RType.OHLCV_1H if case == "rtype" else db.RType.OHLCV_1M)
    )
    if case == "publisher":
        connection.decode(record, NOW)
        record = bar(publisher_id=2)
    with pytest.raises(ValueError):
        connection.decode(record, START if case == "early" else NOW)


def test_native_queue_bounds_timeouts_gateway_errors_and_disconnect() -> None:
    client = SyntheticClient()
    connection = DatabentoConnection(source(queue_capacity=1), client, db)
    with pytest.raises(TimeoutError):
        connection.recv(timeout=0)
    connection.on_record(mapping())
    connection.on_record(bar())
    with pytest.raises(ValueError, match="queue integrity"):
        connection.recv(timeout=0)
    connection = DatabentoConnection(source(), client, db)
    client.connected = False
    connection.on_record(mapping())
    connection.on_record(bar())
    with pytest.raises(FeedDisconnected):
        connection.recv(timeout=0)
    for record in (
        db.ErrorMsg(nanos(NOW), "synthetic private error body"),
        db.SystemMsg(nanos(NOW), "synthetic slow reader", db.SystemCode.SLOW_READER_WARNING),
    ):
        with pytest.raises(ValueError) as failure:
            connection.decode(record, NOW)
        assert "private error body" not in str(failure.value)


def test_native_transport_uses_shared_monitor_and_preserves_recovery_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = fixture_monitor(tmp_path)
    transport = source(start=START, key_env="AURUM_SYNTHETIC_KEY", resume_from=lambda: AT)
    monkeypatch.setenv(transport.key_env, "synthetic-key-only")
    client = SyntheticClient(
        (
            mapping(),
            bar(),
            db.SystemMsg(nanos(NOW), "synthetic caught up", db.SystemCode.REPLAY_COMPLETED),
            bar(AT + MINUTE),
        )
    )
    disconnected = SyntheticClient()

    def unavailable(**_: Any) -> None:
        raise OSError("synthetic private failure body")

    monkeypatch.setattr(disconnected, "subscribe", unavailable)
    attempts = 0

    def live(**options: Any) -> SyntheticClient:
        nonlocal attempts
        attempts += 1
        assert options == {
            "key": "synthetic-key-only",
            "reconnect_policy": "none",
            "slow_reader_behavior": "warn",
            "heartbeat_interval_s": 5,
        }
        return disconnected if attempts == 1 else client

    monkeypatch.setattr(db, "Live", live)
    delays: list[float] = []
    result = run_stream(monitor, transport, max_frames=2, clock=lambda: NOW, sleeper=delays.append)
    assert delays == [1] and disconnected.terminated
    assert client.terminated and client.subscription["symbols"] == ["MGCJ5"]
    assert client.subscription["stype_in"] == "raw_symbol"
    assert client.subscription["start"] == AT
    assert result["last_applied_open"] == (AT + MINUTE).isoformat()
    with monitor.store.connection() as sql:
        rows = sql.execute(
            "SELECT source_status, execution_eligible, raw FROM monitor_minutes "
            "WHERE run_id = ? AND opened_at >= ? ORDER BY opened_at",
            (monitor.monitor_id, AT.isoformat()),
        ).fetchall()
    assert [(row["source_status"], row["execution_eligible"]) for row in rows] == [
        ("historical", 0),
        ("real_time", 1),
    ]
    assert json.loads(rows[0]["raw"])["adapter"] == transport.definition()
    assert "synthetic-key-only" not in repr(transport) + str(result)


def test_native_integrity_fault_latches_monitor_without_accepting_queued_bars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = fixture_monitor(tmp_path)
    monkeypatch.setenv("AURUM_SYNTHETIC_KEY", "synthetic-key-only")
    client = SyntheticClient((mapping(), bar()))
    monkeypatch.setattr(db, "Live", lambda **_: client)
    result = run_stream(
        monitor,
        source(key_env="AURUM_SYNTHETIC_KEY", queue_capacity=1),
        max_frames=1,
        clock=lambda: NOW,
    )
    assert result["state"] == MonitorState.INVALID and result["alerts_suspended"] is True
    assert result["last_applied_open"] == START.isoformat()
    assert client.terminated


@pytest.mark.parametrize("future_expected", (False, True))
def test_native_current_or_future_schedule_cursor_uses_live_only_subscription(
    monkeypatch: pytest.MonkeyPatch,
    future_expected: bool,
) -> None:
    monkeypatch.setenv("AURUM_SYNTHETIC_KEY", "synthetic-key-only")
    client = SyntheticClient()
    monkeypatch.setattr(db, "Live", lambda **_: client)
    cursor = NOW + MINUTE if future_expected else NOW
    transport = source(key_env="AURUM_SYNTHETIC_KEY", resume_from=lambda: cursor)
    with transport.connect():
        assert client.subscription["start"] is None
    assert client.terminated


def test_native_missing_key_and_old_replay_fail_before_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AURUM_SYNTHETIC_MISSING", raising=False)
    with (
        pytest.raises(ValueError, match="unset"),
        source(key_env="AURUM_SYNTHETIC_MISSING").connect(),
    ):
        pass
    monkeypatch.setenv("AURUM_SYNTHETIC_KEY", "synthetic-key-only")
    with (
        pytest.raises(ValueError, match="23 hours"),
        source(key_env="AURUM_SYNTHETIC_KEY", start=NOW - timedelta(days=2)).connect(),
    ):
        pass
    assert replace(source(), key_env="ROTATED_KEY_NAME").fingerprint == source().fingerprint
    with pytest.raises(ValueError, match="dated MGC"):
        DatabentoSource(CONTRACT, "MGC.c.0")


def test_native_cli_requires_local_key_and_does_not_expose_it(tmp_path: Path) -> None:
    monitor = fixture_monitor(tmp_path)
    calendar = tmp_path / "synthetic-calendar.json"
    calendar.write_text(json.dumps(monitor.calendar.as_dict()))
    result = subprocess.run(
        [
            str(Path(executable).with_name("aurum")),
            "--db",
            str(monitor.store.path),
            "monitor-databento",
            "--calendar",
            str(calendar),
            "--contract",
            CONTRACT.code,
            "--raw-symbol",
            "MGCJ5",
            "--key-env",
            "AURUM_SYNTHETIC_MISSING",
            "--max-frames",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (
        result.returncode == 1
        and "access variable AURUM_SYNTHETIC_MISSING is unset" in result.stderr
    )
