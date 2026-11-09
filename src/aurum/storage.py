import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TypedDict

from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.evidence import EVIDENCE_VERSION, EvidenceConfig, EvidenceEngine, EvidenceEvent
from aurum.exchange_calendar import SessionCalendar
from aurum.instruments import MgcContract
from aurum.market_data import Candle, PreparedDataset, canonical_json
from aurum.market_structure import STRUCTURE_VERSION, StructureConfig, StructureEngine

APPLICATION_ID = 0x4155524D
SCHEMA_VERSION = 2

SCHEMA_V1 = (
    """CREATE TABLE source_blobs (
        sha256 TEXT PRIMARY KEY NOT NULL,
        raw BLOB NOT NULL
    ) STRICT""",
    """CREATE TABLE datasets (
        id TEXT PRIMARY KEY NOT NULL,
        source_sha256 TEXT NOT NULL REFERENCES source_blobs(sha256),
        provenance TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        first_open TEXT NOT NULL,
        last_close TEXT NOT NULL,
        candle_count INTEGER NOT NULL CHECK (candle_count > 0),
        issues TEXT NOT NULL,
        replayable INTEGER NOT NULL CHECK (replayable IN (0, 1))
    ) STRICT""",
    """CREATE TABLE candles (
        dataset_id TEXT NOT NULL REFERENCES datasets(id),
        opened_at TEXT NOT NULL,
        open_ticks INTEGER NOT NULL,
        high_ticks INTEGER NOT NULL,
        low_ticks INTEGER NOT NULL,
        close_ticks INTEGER NOT NULL,
        volume INTEGER NOT NULL CHECK (volume >= 0),
        PRIMARY KEY (dataset_id, opened_at),
        CHECK (0 < low_ticks AND low_ticks <= min(open_ticks, close_ticks)
               AND max(open_ticks, close_ticks) <= high_ticks)
    ) STRICT, WITHOUT ROWID""",
)

SCHEMA_V2 = (
    """CREATE TABLE structure_runs (
        id TEXT PRIMARY KEY NOT NULL,
        dataset_id TEXT NOT NULL REFERENCES datasets(id),
        definition TEXT NOT NULL,
        created_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE structure_events (
        run_id TEXT NOT NULL REFERENCES structure_runs(id),
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        payload TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence)
    ) STRICT, WITHOUT ROWID""",
)


class DatasetDescription(TypedDict):
    dataset_id: str
    imported_at: str
    first_open: str
    last_close: str
    candle_count: int
    replayable: bool
    provenance: dict[str, object]
    issues: list[dict[str, object]]


class DatasetStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, dataset: PreparedDataset) -> str:
        with self._connection(write=True) as db:
            exists = db.execute("SELECT 1 FROM datasets WHERE id = ?", (dataset.dataset_id,))
            if exists.fetchone() is not None:
                return dataset.dataset_id
            db.execute(
                "INSERT INTO source_blobs VALUES (?, ?) ON CONFLICT (sha256) DO NOTHING",
                (dataset.source_sha256, dataset.raw),
            )
            db.execute(
                """INSERT INTO datasets
                   (id, source_sha256, provenance, imported_at, first_open, last_close,
                    candle_count, issues, replayable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dataset.dataset_id,
                    dataset.source_sha256,
                    dataset.provenance_json,
                    datetime.now(UTC).isoformat(),
                    dataset.candles[0].opened_at.isoformat(),
                    dataset.candles[-1].available_at.isoformat(),
                    len(dataset.candles),
                    canonical_json([asdict(issue) for issue in dataset.issues]),
                    int(all(issue.code != "missing_candles" for issue in dataset.issues)),
                ),
            )
            db.executemany(
                """INSERT INTO candles
                   (dataset_id, opened_at, open_ticks, high_ticks, low_ticks, close_ticks, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        dataset.dataset_id,
                        candle.opened_at.isoformat(),
                        candle.open_ticks,
                        candle.high_ticks,
                        candle.low_ticks,
                        candle.close_ticks,
                        candle.volume,
                    )
                    for candle in dataset.candles
                ),
            )
        return dataset.dataset_id

    def describe(self, dataset_id: str) -> DatasetDescription:
        with self._connection() as db:
            row = self._dataset(db, dataset_id)
            return {
                "dataset_id": row["id"],
                "imported_at": row["imported_at"],
                "first_open": row["first_open"],
                "last_close": row["last_close"],
                "candle_count": row["candle_count"],
                "replayable": bool(row["replayable"]),
                "provenance": json.loads(row["provenance"]),
                "issues": json.loads(row["issues"]),
            }

    def source_bytes(self, dataset_id: str) -> bytes:
        with self._connection() as db:
            row = self._dataset(db, dataset_id)
            source = db.execute(
                "SELECT raw FROM source_blobs WHERE sha256 = ?", (row["source_sha256"],)
            ).fetchone()
            return bytes(source["raw"])

    def replay(self, dataset_id: str) -> Iterator[Candle]:
        with self._connection() as db:
            yield from self._replay(db, self._dataset(db, dataset_id))

    def build_structure_run(
        self,
        dataset_id: str,
        timeframe: Timeframe,
        config: StructureConfig,
        *,
        evidence: EvidenceConfig | None = None,
    ) -> str:
        manifest: dict[str, object] = {
            "dataset_id": dataset_id,
            "timeframe": timeframe,
            "aggregation_version": AGGREGATION_VERSION,
            "structure_version": STRUCTURE_VERSION,
            "config": asdict(config),
        }
        if evidence is not None:
            manifest.update(evidence_version=EVIDENCE_VERSION, evidence_config=asdict(evidence))
        definition = canonical_json(manifest)
        run_id = sha256(definition.encode()).hexdigest()
        with self._connection(write=True) as db:
            if db.execute("SELECT 1 FROM structure_runs WHERE id = ?", (run_id,)).fetchone():
                return run_id
            dataset = self._dataset(db, dataset_id)
            provenance = json.loads(dataset["provenance"])
            calendar = SessionCalendar.from_dict(provenance["calendar"])
            contract = MgcContract(provenance["contract"])
            db.execute(
                "INSERT INTO structure_runs VALUES (?, ?, ?, ?)",
                (run_id, dataset_id, definition, datetime.now(UTC).isoformat()),
            )
            minutes = self._replay(db, dataset)
            events: Iterator[EvidenceEvent]
            if evidence is None:
                aggregator = CandleAggregator(calendar, contract, (timeframe,))
                engine = StructureEngine(contract, timeframe, config)
                events = (
                    event for bar in aggregator.consume(minutes) for event in engine.push(bar)
                )
            else:
                events = EvidenceEngine(calendar, contract, timeframe, config, evidence).consume(
                    minutes
                )
            db.executemany(
                "INSERT INTO structure_events VALUES (?, ?, ?)",
                (
                    (run_id, sequence, canonical_json(event.as_dict()))
                    for sequence, event in enumerate(events, start=1)
                ),
            )
        return run_id

    def describe_structure_run(self, run_id: str) -> dict[str, object]:
        with self._connection() as db:
            run = self._structure_run(db, run_id)
            count = db.execute(
                "SELECT max(sequence) FROM structure_events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            provenance = json.loads(run["dataset_provenance"])
            return {
                **json.loads(run["definition"]),
                "run_id": run_id,
                "data_kind": provenance["data_kind"],
                "contract": provenance["contract"],
                "created_at": run["created_at"],
                "event_count": count or 0,
            }

    def structure_events(self, run_id: str) -> Iterator[dict[str, object]]:
        with self._connection() as db:
            run = self._structure_run(db, run_id)
            definition = json.loads(run["definition"])
            provenance = json.loads(run["dataset_provenance"])
            for event in db.execute(
                "SELECT sequence, payload FROM structure_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ):
                yield {
                    **json.loads(event["payload"]),
                    "run_id": run_id,
                    "event_id": f"{run_id}/{event['sequence']}",
                    "dataset_id": run["dataset_id"],
                    "data_kind": provenance["data_kind"],
                    "contract": provenance["contract"],
                    "timeframe": definition["timeframe"],
                    "aggregation_version": definition["aggregation_version"],
                    "structure_version": definition["structure_version"],
                }

    @staticmethod
    def _structure_run(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
            row = db.execute(
                """SELECT r.*, d.provenance AS dataset_provenance
                   FROM structure_runs AS r JOIN datasets AS d ON r.dataset_id = d.id
                   WHERE r.id = ?""",
                (run_id,),
            ).fetchone()
            if isinstance(row, sqlite3.Row):
                return row
        raise ValueError(f"unknown structure run: {run_id}")

    @staticmethod
    def _replay(db: sqlite3.Connection, dataset: sqlite3.Row) -> Iterator[Candle]:
        if not dataset["replayable"]:
            raise ValueError("replay blocked because expected candles are missing")
        contract = MgcContract(json.loads(dataset["provenance"])["contract"])
        rows = db.execute(
            """SELECT opened_at, open_ticks, high_ticks, low_ticks, close_ticks, volume
               FROM candles WHERE dataset_id = ? ORDER BY opened_at""",
            (dataset["id"],),
        )
        for candle in rows:
            yield Candle(
                contract,
                datetime.fromisoformat(candle["opened_at"]),
                candle["open_ticks"],
                candle["high_ticks"],
                candle["low_ticks"],
                candle["close_ticks"],
                candle["volume"],
            )

    @staticmethod
    def _dataset(db: sqlite3.Connection, dataset_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if not isinstance(row, sqlite3.Row):
            raise ValueError(f"unknown dataset: {dataset_id}")
        return row

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode={'rwc' if write else 'ro'}", uri=True
        )
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("PRAGMA foreign_keys = ON")
                if write:
                    db.execute("BEGIN IMMEDIATE")
                self._check_schema(db, write=write)
                yield db
        finally:
            db.close()

    @staticmethod
    def _check_schema(db: sqlite3.Connection, *, write: bool) -> None:
        app_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if app_id != APPLICATION_ID:
            if (
                not write
                or app_id
                or version
                or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
            ):
                raise ValueError("database is not an AURUM store with a supported schema version")
            for statement in SCHEMA_V1:
                db.execute(statement)
            db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        elif not 1 <= version <= SCHEMA_VERSION:
            raise ValueError("database is not an AURUM store with a supported schema version")

        if write and version < 2:
            for statement in SCHEMA_V2:
                db.execute(statement)
            db.execute("PRAGMA user_version = 2")
