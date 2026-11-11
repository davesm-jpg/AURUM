import os
import sqlite3
from contextlib import closing
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from aurum.dataset_audit import verify_dataset
from aurum.economic_archive import verify_economic_capture
from aurum.economic_polling import verify_economic_poller
from aurum.monitor_audit import verify_monitor
from aurum.quotes import verify_quote_series
from aurum.storage import DatasetStore


def verify_store(
    store: DatasetStore,
    *,
    full: bool = False,
    verify_sources: bool = False,
    verify_normalized: bool = False,
) -> dict[str, object]:
    """Validate SQLite and optionally audit source bytes or their normalized rows."""
    with store.connection() as db:
        db.execute("BEGIN")
        check = "integrity_check" if full else "quick_check"
        (result,) = db.execute(f"PRAGMA {check}(1)").fetchone()
        if result != "ok":
            raise ValueError("SQLite integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SQLite foreign-key integrity check failed")
        verified = 0
        if verify_sources:
            for row in db.execute("SELECT rowid, sha256 FROM source_blobs ORDER BY rowid"):
                digest = sha256()
                with db.blobopen("source_blobs", "raw", row["rowid"], readonly=True) as raw:
                    while chunk := raw.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != row["sha256"]:
                    raise ValueError("preserved source checksum mismatch")
                verified += 1
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        summary: dict[str, object] = {
            "schema_version": schema,
            "integrity": check,
            "foreign_keys": "verified",
            "source_checksums_verified": verified if verify_sources else None,
        }
        if not verify_normalized:
            return summary
        datasets = tuple(row["id"] for row in db.execute("SELECT id FROM datasets ORDER BY id"))
        quote_series = (
            tuple(row["id"] for row in db.execute("SELECT id FROM quote_series ORDER BY id"))
            if schema >= 13
            else ()
        )
        economic_captures = (
            tuple(row["id"] for row in db.execute("SELECT id FROM economic_captures ORDER BY id"))
            if schema >= 14
            else ()
        )
        pollers = (
            tuple(row[0] for row in db.execute("SELECT id FROM economic_pollers ORDER BY id"))
            if schema >= 16
            else ()
        )
        monitors = (
            tuple(row[0] for row in db.execute("SELECT id FROM monitor_runs ORDER BY id"))
            if schema >= 4
            else ()
        )

    blocked = 0
    for dataset_id in datasets:
        try:
            audit = verify_dataset(store, dataset_id)
        except ValueError as exc:
            raise ValueError(f"dataset {dataset_id} failed normalization audit: {exc}") from exc
        blocked += audit["replayable"] is False
    for series_id in quote_series:
        try:
            verify_quote_series(store, series_id)
        except ValueError as exc:
            raise ValueError(f"quote series {series_id} failed normalization audit: {exc}") from exc
    for capture_id in economic_captures:
        try:
            verify_economic_capture(store, capture_id)
        except ValueError as exc:
            raise ValueError(f"economic capture {capture_id} failed archive audit: {exc}") from exc
    poll_attempts = 0
    for poller_id in pollers:
        try:
            poll_attempts += verify_economic_poller(store, poller_id)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"economic poller {poller_id} failed journal audit: {exc}") from exc
    monitor_minutes = 0
    for monitor_id in monitors:
        try:
            monitor_minutes += int(str(verify_monitor(store, monitor_id)["minutes_verified"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"monitor {monitor_id} failed input audit: {exc}") from exc
    summary["normalized"] = {
        "datasets_verified": len(datasets),
        "quote_series_verified": len(quote_series),
        "datasets_blocked_for_replay": blocked,
        **(
            {"monitors_verified": len(monitors), "monitor_minutes_verified": monitor_minutes}
            if monitors
            else {}
        ),
        **({"economic_captures_verified": len(economic_captures)} if economic_captures else {}),
        **(
            {
                "economic_pollers_verified": len(pollers),
                "economic_poll_attempts_verified": poll_attempts,
            }
            if pollers
            else {}
        ),
    }
    return summary


def backup_store(
    store: DatasetStore,
    destination: Path,
    *,
    timeout_seconds: float = 120,
    verify_normalized: bool = False,
) -> dict[str, object]:
    """Publish a validated private snapshot without replacing an existing file."""
    if not 0 < timeout_seconds <= 3600:
        raise ValueError("backup timeout must be between 0 and 3600 seconds")
    if destination.exists() or destination.is_symlink():
        raise ValueError("backup destination already exists")
    deadline = monotonic() + timeout_seconds

    def check_time(status: int, remaining: int, total: int) -> None:
        if monotonic() > deadline:
            raise TimeoutError("SQLite backup timed out")

    with TemporaryDirectory(prefix=".aurum-backup-", dir=destination.parent) as private:
        staging = Path(private) / "snapshot.sqlite"
        staging.touch(mode=0o600, exist_ok=False)
        with store.connection() as source, closing(sqlite3.connect(staging)) as target:
            source.backup(target, pages=256, sleep=0.025, progress=check_time)
            if target.execute("PRAGMA journal_mode = DELETE").fetchone()[0] != "delete":
                raise ValueError("backup could not be made self-contained")
        verified = verify_store(DatasetStore(staging), verify_normalized=verify_normalized)
        with staging.open("rb") as snapshot:
            os.fsync(snapshot.fileno())
            size = os.fstat(snapshot.fileno()).st_size
            checksum = file_digest(snapshot, "sha256").hexdigest()
        try:
            os.link(staging, destination)
        except FileExistsError:
            raise ValueError("backup destination already exists") from None
    return {**verified, "path": str(destination), "bytes": size, "sha256": checksum}
