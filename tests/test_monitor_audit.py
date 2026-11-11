"""Preserved monitor input checks use labeled synthetic fixtures only."""

import json
import sqlite3
import subprocess
from hashlib import sha256
from pathlib import Path
from sys import executable

import pytest
from test_monitoring import CONTRACT, START, calendar, fixture_monitor, frame

from aurum.feed_frames import FeedFrame
from aurum.market_data import Candle, canonical_json
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitor_audit import verify_monitor
from aurum.monitoring import MarketMonitor, MonitorConfig, MonitorState, describe_monitor
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.storage_admin import backup_store
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
from aurum.timestamps import MINUTE


def prepared(tmp_path: Path, count: int = 6) -> MarketMonitor:
    store = DatasetStore(tmp_path / "synthetic-monitor-audit.sqlite")
    monitor = fixture_monitor(store, calendar((0, count)), FeedStatus.SIMULATED)
    for offset in range(0, count, 256):
        monitor.ingest_replay_batch(
            tuple(
                frame(index, mode=FeedStatus.SIMULATED)
                for index in range(offset, min(offset + 256, count))
            )
        )
    return monitor


def test_monitor_audit_reads_bounded_source_pages_and_cli_without_mutation(tmp_path: Path) -> None:
    monitor = prepared(tmp_path, 1002)
    before = sha256(monitor.store.path.read_bytes()).hexdigest()
    report = verify_monitor(monitor.store, monitor.monitor_id)
    assert report["minutes_verified"] == report["applied_minutes_verified"] == 1002
    assert report["legacy_unknown_observation_rows"] == 0
    assert report["scope"] == "preserved_input_equivalence_and_applied_continuity"
    command = [
        str(Path(executable).with_name("aurum")),
        "--db",
        str(monitor.store.path),
        "verify-monitor",
        monitor.monitor_id,
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == report
    assert sha256(monitor.store.path.read_bytes()).hexdigest() == before
    with sqlite3.connect(monitor.store.path) as db:
        db.execute(
            "UPDATE monitor_minutes SET candle = replace(candle, '\"volume\":2', '\"volume\":3') "
            "WHERE opened_at = (SELECT max(opened_at) FROM monitor_minutes)"
        )
    with pytest.raises(ValueError, match="differs from its preserved frame"):
        verify_monitor(monitor.store, monitor.monitor_id)


def test_monitor_audit_includes_pending_frames_and_preserves_unknown_legacy_clock(
    tmp_path: Path,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-pending.sqlite")
    monitor = fixture_monitor(store, calendar((0, 6)), FeedStatus.SIMULATED)
    monitor.ingest(frame(3, received=4, mode=FeedStatus.SIMULATED))
    pending = verify_monitor(store, monitor.monitor_id)
    assert pending["minutes_verified"] == 1 and pending["applied_minutes_verified"] == 0
    monitor.ingest(frame(0, received=5, mode=FeedStatus.SIMULATED))
    with store.connection(write=True) as db:
        db.execute(
            "UPDATE monitor_minutes SET applied_at = NULL, execution_eligible = 0 WHERE applied = 1"
        )
    report = verify_monitor(store, monitor.monitor_id)
    assert report["minutes_verified"] == 2 and report["applied_minutes_verified"] == 1
    assert report["legacy_unknown_observation_rows"] == 1


@pytest.mark.parametrize("unknown_middle", (False, True))
def test_monitor_audit_rejects_regressing_known_observations_and_backup(
    tmp_path: Path, unknown_middle: bool
) -> None:
    store = DatasetStore(tmp_path / "synthetic-regressed-observations.sqlite")
    monitor = fixture_monitor(store, calendar((0, 6)), FeedStatus.SIMULATED)
    later = START + 5 * MINUTE
    for index in range(3):
        monitor.ingest(frame(index, mode=FeedStatus.SIMULATED), application_clock=lambda: later)
    assert verify_monitor(store, monitor.monitor_id)["verified"] is True
    with store.connection(write=True) as db:
        db.execute(
            "UPDATE monitor_minutes SET applied_at = ? WHERE opened_at = ?",
            ((START + 4 * MINUTE).isoformat(), (START + 2 * MINUTE).isoformat()),
        )
        if unknown_middle:
            db.execute(
                "UPDATE monitor_minutes SET applied_at = NULL WHERE opened_at = ?",
                ((START + MINUTE).isoformat(),),
            )
    with pytest.raises(ValueError, match="known monitor observations regress"):
        verify_monitor(store, monitor.monitor_id)
    destination = tmp_path / "synthetic-rejected-clock-backup.sqlite"
    with pytest.raises(ValueError, match="known monitor observations regress"):
        backup_store(store, destination, verify_normalized=True)
    assert not destination.exists()


@pytest.mark.parametrize(
    "mutation",
    (
        "UPDATE monitor_minutes SET raw = x'7b7d'",
        "UPDATE monitor_minutes SET received_at = '2025-03-10T13:00:00+00:00'",
        "UPDATE monitor_minutes SET applied = 0, applied_at = NULL, execution_eligible = NULL",
        "UPDATE monitor_runs SET definition = replace(definition, 'synthetic', 'changed')",
        "UPDATE monitor_minutes SET execution_eligible = 1, "
        "applied_at = '2025-03-10T15:00:00+00:00'",
    ),
)
def test_monitor_corruption_prevents_verified_backup_publication(
    tmp_path: Path, mutation: str
) -> None:
    monitor = prepared(tmp_path)
    assert backup_store(monitor.store, tmp_path / "synthetic-good.sqlite", verify_normalized=True)
    with sqlite3.connect(monitor.store.path) as db:
        db.execute(mutation)
    rejected = tmp_path / "synthetic-rejected.sqlite"
    with pytest.raises(ValueError, match="failed input audit"):
        backup_store(monitor.store, rejected, verify_normalized=True)
    assert not rejected.exists()


def test_monitor_audit_reconstructs_normalized_websocket_frames(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic-normalized.sqlite")
    monitor = fixture_monitor(store, calendar((0, 2)), FeedStatus.SIMULATED)
    candle = Candle(CONTRACT, START, 29000, 29000, 29000, 29000, 1)
    payload = {key: value for key, value in candle.as_dict().items() if key != "available_at"}
    payload.update(event="candle_closed", source_status="simulated")
    monitor.ingest(
        FeedFrame.from_message(canonical_json(payload), FeedStatus.SIMULATED, START + MINUTE)
    )
    assert verify_monitor(store, monitor.monitor_id)["minutes_verified"] == 1
    with store.connection(write=True) as db:
        db.execute("UPDATE monitor_minutes SET source_status = 'historical'")
    with pytest.raises(ValueError, match="classification differs"):
        verify_monitor(store, monitor.monitor_id)


def test_monitor_restart_latches_failed_source_verification_before_research_resumes(
    tmp_path: Path,
) -> None:
    monitor = prepared(tmp_path)
    with sqlite3.connect(monitor.store.path) as db:
        db.execute(
            "UPDATE monitor_minutes SET candle = replace(candle, '\"volume\":2', '\"volume\":3')"
        )
    with pytest.raises(ValueError, match="differs from its preserved frame"):
        fixture_monitor(monitor.store, calendar((0, 6)), FeedStatus.SIMULATED)
    status = describe_monitor(monitor.store, monitor.monitor_id)
    assert status["state"] == MonitorState.INVALID and status["alerts_suspended"] is True
    assert status["fault"] == "checkpoint_source_integrity_failure"


def test_monitor_audit_reconstructs_native_records_without_client_or_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = pytest.importorskip("databento")
    from test_databento_live import bar, mapping

    from aurum.databento_live import DatabentoConnection, DatabentoSource

    source = DatabentoSource(CONTRACT, "MGCJ5")
    store = DatasetStore(tmp_path / "synthetic-native-audit.sqlite")
    monitor = MarketMonitor(
        store,
        calendar((0, 6)),
        CONTRACT,
        MonitorConfig(
            "synthetic native input audit",
            FeedStatus.REAL_TIME,
            source_origin=source.origin,
            source_fingerprint=source.fingerprint,
        ),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
    )
    decoder = DatabentoConnection(source, None, db)
    decoder.decode(mapping(), START + MINUTE)
    native = decoder.decode(bar(START), START + MINUTE)
    assert native is not None
    monitor.ingest(native)

    def forbidden(**_: object) -> None:
        raise AssertionError("offline audit must never create a live client")

    monkeypatch.setattr(db, "Live", forbidden)
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    assert verify_monitor(store, monitor.monitor_id)["minutes_verified"] == 1
    payload = json.loads(native.raw)
    payload["ohlcv_dbn_hex"] += "00"
    with store.connection(write=True) as sql:
        sql.execute("UPDATE monitor_minutes SET raw = ?", (canonical_json(payload).encode(),))
    with pytest.raises(ValueError, match="preserved native frame evidence"):
        verify_monitor(store, monitor.monitor_id)
