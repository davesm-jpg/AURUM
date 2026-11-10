import json
import sqlite3
import subprocess
from datetime import date
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE
from sys import executable

import pytest

from aurum import storage_admin
from aurum.dataset_audit import verify_dataset
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.storage import SCHEMA_VERSION, DatasetStore
from aurum.storage_admin import backup_store, verify_store
from aurum.timestamps import MINUTE, minute_timestamp


def _synthetic_store(tmp_path: Path) -> tuple[DatasetStore, str]:
    at = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(at, at + MINUTE)
    calendar = SessionCalendar(
        "synthetic storage fixture",
        "synthetic-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    contract = MgcContract("MGC-2025-04")
    source = tmp_path / "synthetic.csv"
    source.write_text(
        "timestamp,contract,open,high,low,close,volume\n"
        f"{at.isoformat()},{contract.code},2900,2900,2900,2900,1\n",
        encoding="utf-8",
    )
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    config = ImportConfig("synthetic", contract, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC)
    return store, store.save(prepare_csv(source, config, calendar))


def test_cli_backup_reopens_as_an_independent_verified_store(tmp_path: Path) -> None:
    source, dataset = _synthetic_store(tmp_path)
    destination = tmp_path / "synthetic-backup.sqlite"
    cli = [str(Path(executable).with_name("aurum")), "--db", str(source.path)]
    result = subprocess.run(
        [*cli, "backup", str(destination), "--verify-normalized"],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(result.stdout)
    assert metadata["schema_version"] == SCHEMA_VERSION
    assert metadata["normalized"]["datasets_verified"] == 1
    assert metadata["sha256"] == sha256(destination.read_bytes()).hexdigest()
    assert metadata["bytes"] == destination.stat().st_size
    assert S_IMODE(destination.stat().st_mode) == 0o600
    copy = DatasetStore(destination)
    assert copy.source_bytes(dataset) == source.source_bytes(dataset)
    assert list(copy.replay(dataset)) == list(source.replay(dataset))
    assert verify_store(copy, full=True, verify_sources=True)["source_checksums_verified"] == 1
    assert verify_dataset(copy, dataset)["verified"] is True
    audit = subprocess.run(
        [
            str(Path(executable).with_name("aurum")),
            "--db",
            str(destination),
            "verify-store",
            "--verify-sources",
            "--verify-normalized",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(audit.stdout)["normalized"] == {
        "datasets_verified": 1,
        "quote_series_verified": 0,
        "datasets_blocked_for_replay": 0,
    }
    with pytest.raises(ValueError, match="already exists"):
        backup_store(source, destination)
    assert metadata["sha256"] == sha256(destination.read_bytes()).hexdigest()


def test_aborted_backup_never_publishes_a_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = _synthetic_store(tmp_path)
    destination = tmp_path / "synthetic-backup.sqlite"
    times = iter((0.0, 2.0))
    monkeypatch.setattr(storage_admin, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError, match="timed out"):
        backup_store(source, destination, timeout_seconds=1)
    assert not destination.exists()
    assert not list(tmp_path.glob(".aurum-backup-*"))


def test_source_verification_detects_modified_synthetic_bytes(tmp_path: Path) -> None:
    source, dataset = _synthetic_store(tmp_path)
    with sqlite3.connect(source.path) as db:
        db.execute("UPDATE source_blobs SET raw = ?", (b"altered synthetic fixture",))
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_store(source, verify_sources=True)
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_dataset(source, dataset)


def test_cli_verifies_normalized_dataset_and_detects_tampered_values(tmp_path: Path) -> None:
    store, dataset = _synthetic_store(tmp_path)
    cli = [
        str(Path(executable).with_name("aurum")),
        "--db",
        str(store.path),
        "verify-dataset",
        dataset,
    ]
    verified = subprocess.run(cli, check=True, capture_output=True, text=True)
    result = json.loads(verified.stdout)
    assert result == verify_dataset(store, dataset)
    assert result["verified"] is True and result["data_kind"] == "synthetic"
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE candles SET volume = volume + 1 WHERE dataset_id = ?", (dataset,))
    assert verify_store(store, verify_sources=True)["source_checksums_verified"] == 1
    with pytest.raises(ValueError, match=f"dataset {dataset} failed normalization audit"):
        verify_store(store, verify_normalized=True)
    backup = tmp_path / "synthetic-invalid-backup.sqlite"
    with pytest.raises(ValueError, match="failed normalization audit"):
        backup_store(store, backup, verify_normalized=True)
    assert not backup.exists()
    assert not list(tmp_path.glob(".aurum-backup-*"))
    rejected = subprocess.run(cli, check=False, capture_output=True, text=True)
    assert rejected.returncode == 1
    assert "stored normalized candle differs" in rejected.stderr
    assert rejected.stdout == ""
