import os
import sqlite3
from contextlib import closing
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from aurum.storage import DatasetStore


def verify_store(
    store: DatasetStore, *, full: bool = False, verify_sources: bool = False
) -> dict[str, object]:
    """Validate the SQLite snapshot and optionally verify every preserved source checksum."""
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
        return {
            "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            "integrity": check,
            "foreign_keys": "verified",
            "source_checksums_verified": verified if verify_sources else None,
        }


def backup_store(
    store: DatasetStore, destination: Path, *, timeout_seconds: float = 120
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
        verified = verify_store(DatasetStore(staging))
        with staging.open("rb") as snapshot:
            os.fsync(snapshot.fileno())
            size = os.fstat(snapshot.fileno()).st_size
            checksum = file_digest(snapshot, "sha256").hexdigest()
        try:
            os.link(staging, destination)
        except FileExistsError:
            raise ValueError("backup destination already exists") from None
    return {**verified, "path": str(destination), "bytes": size, "sha256": checksum}
