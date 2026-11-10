import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Protocol, TypedDict

from aurum.backtesting import BACKTEST_VERSION, BacktestReplay
from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.economic_events import EconomicCalendar
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EVIDENCE_VERSION, EvidenceConfig, EvidenceEngine
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.execution import EXECUTION_VERSION, ExecutionConfig
from aurum.instruments import MgcContract
from aurum.market_context import CONTEXT_VERSION, DEFAULT_CONTEXT_CONFIG, ContextConfig
from aurum.market_data import Candle, PreparedDataset, canonical_json
from aurum.market_structure import STRUCTURE_VERSION, StructureConfig, StructureEngine
from aurum.performance import PERFORMANCE_VERSION
from aurum.strategy import STRATEGY_VERSION, StrategyConfig, StrategyReplay

APPLICATION_ID = 0x4155524D
SCHEMA_VERSION = 11

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

SCHEMA_V3 = """CREATE TABLE analysis_reports (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    definition TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT"""

SCHEMA_V4 = (
    """CREATE TABLE monitor_runs (
        id TEXT PRIMARY KEY NOT NULL,
        definition TEXT NOT NULL,
        state TEXT NOT NULL,
        connected INTEGER NOT NULL CHECK (connected IN (0, 1)),
        next_open TEXT,
        last_applied TEXT,
        last_received TEXT,
        checked_at TEXT,
        fault TEXT
    ) STRICT""",
    """CREATE TABLE monitor_minutes (
        run_id TEXT NOT NULL REFERENCES monitor_runs(id),
        opened_at TEXT NOT NULL,
        candle TEXT NOT NULL,
        raw BLOB NOT NULL,
        received_at TEXT NOT NULL,
        source_status TEXT NOT NULL,
        applied INTEGER NOT NULL CHECK (applied IN (0, 1)),
        PRIMARY KEY (run_id, opened_at)
    ) STRICT, WITHOUT ROWID""",
    """CREATE INDEX monitor_pending ON monitor_minutes(run_id, opened_at) WHERE applied = 0""",
    """CREATE TABLE monitor_events (
        run_id TEXT NOT NULL REFERENCES monitor_runs(id),
        sequence INTEGER NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE monitor_alerts (
        run_id TEXT NOT NULL REFERENCES monitor_runs(id),
        alert_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, alert_id)
    ) STRICT, WITHOUT ROWID""",
)

SCHEMA_V5 = (
    """CREATE TABLE notification_deliveries (
        id TEXT PRIMARY KEY NOT NULL,
        monitor_id TEXT NOT NULL,
        alert_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        destination_key TEXT NOT NULL,
        state TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        due_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        claimed_at TEXT,
        last_error TEXT,
        FOREIGN KEY (monitor_id, alert_id) REFERENCES monitor_alerts(run_id, alert_id),
        UNIQUE (monitor_id, alert_id, channel, destination_key)
    ) STRICT""",
    """CREATE INDEX notification_due
       ON notification_deliveries(monitor_id, channel, destination_key, state, due_at)""",
    """CREATE TABLE notification_attempts (
        delivery_id TEXT NOT NULL REFERENCES notification_deliveries(id),
        attempt INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome TEXT,
        error TEXT,
        PRIMARY KEY (delivery_id, attempt)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE notification_cancellations (
        monitor_id TEXT NOT NULL,
        alert_id TEXT NOT NULL,
        canceled_at TEXT NOT NULL,
        PRIMARY KEY (monitor_id, alert_id),
        FOREIGN KEY (monitor_id, alert_id) REFERENCES monitor_alerts(run_id, alert_id)
    ) STRICT, WITHOUT ROWID""",
)

SCHEMA_V6 = (
    """ALTER TABLE monitor_minutes ADD COLUMN applied_at TEXT
       CHECK (applied_at IS NULL OR applied = 1)""",
    """CREATE INDEX monitor_observed_start ON monitor_minutes(run_id, opened_at)
       WHERE applied_at IS NOT NULL""",
    """CREATE TABLE monitor_market_events (
        run_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        minute_open TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence),
        FOREIGN KEY (run_id, minute_open) REFERENCES monitor_minutes(run_id, opened_at)
    ) STRICT, WITHOUT ROWID""",
    """CREATE INDEX monitor_market_event_kind
       ON monitor_market_events(run_id, kind, sequence)""",
)

SCHEMA_V7 = (
    """ALTER TABLE monitor_minutes ADD COLUMN execution_eligible INTEGER
       CHECK (execution_eligible IS NULL OR (applied = 1 AND execution_eligible IN (0, 1)))""",
    """CREATE INDEX monitor_signal_close
       ON monitor_events(run_id, json_extract(payload, '$.confirmed_at'))
       WHERE json_extract(payload, '$.event') = 'paper_setup_transition'
         AND json_extract(payload, '$.state') = 'paper_signal'""",
    """CREATE TABLE forward_runs (
        id TEXT PRIMARY KEY NOT NULL,
        monitor_id TEXT NOT NULL REFERENCES monitor_runs(id),
        definition TEXT NOT NULL,
        cursor TEXT,
        pending_setup_id TEXT,
        open_setup_id TEXT
    ) STRICT""",
    """CREATE TABLE forward_events (
        run_id TEXT NOT NULL REFERENCES forward_runs(id),
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        minute_open TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence)
    ) STRICT, WITHOUT ROWID""",
    """CREATE INDEX forward_event_time ON forward_events(run_id, recorded_at, sequence)""",
)

SCHEMA_V8 = (
    """CREATE INDEX forward_event_minute ON forward_events(run_id, minute_open, sequence)""",
    """CREATE TABLE forward_report_publications (
        forward_id TEXT NOT NULL REFERENCES forward_runs(id),
        period TEXT NOT NULL,
        first_trade_date TEXT NOT NULL,
        baseline_key TEXT NOT NULL,
        report_id TEXT NOT NULL REFERENCES analysis_reports(id),
        PRIMARY KEY (forward_id, period, first_trade_date, baseline_key)
    ) STRICT, WITHOUT ROWID""",
)

SCHEMA_V9 = """CREATE TABLE runtime_profiles (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('provider', 'delivery')),
    definition TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT"""

SCHEMA_V10 = (
    """CREATE INDEX monitor_market_minute
       ON monitor_market_events(run_id, minute_open, sequence)""",
    """CREATE INDEX monitor_market_context_minute
       ON monitor_market_events(run_id, minute_open)
       WHERE kind = 'market_context_observed'""",
    """CREATE INDEX monitor_minute_observation
       ON monitor_minutes(run_id, applied_at, opened_at) WHERE applied_at IS NOT NULL""",
    """CREATE INDEX monitor_setup_observation
       ON monitor_events(run_id, json_extract(payload, '$.observed_at'), sequence)
       WHERE json_extract(payload, '$.event') = 'paper_setup_transition'
         AND json_extract(payload, '$.state') = 'paper_signal'""",
)

_STRUCTURE_EVENT_AT = (
    "coalesce(json_extract(payload, '$.recorded_at'), json_extract(payload, '$.confirmed_at'))"
)
SCHEMA_V11 = f"""CREATE INDEX structure_event_available
        ON structure_events(run_id, {_STRUCTURE_EVENT_AT}, sequence)
        WHERE {_STRUCTURE_EVENT_AT} IS NOT NULL"""


class DatasetDescription(TypedDict):
    dataset_id: str
    imported_at: str
    first_open: str
    last_close: str
    candle_count: int
    replayable: bool
    provenance: dict[str, object]
    issues: list[dict[str, object]]


class _RecordedEvent(Protocol):
    def as_dict(self) -> dict[str, object]: ...


class DatasetStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, dataset: PreparedDataset) -> str:
        with self.connection(write=True) as db:
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
        with self.connection() as db:
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
        with self.connection() as db:
            row = self._dataset(db, dataset_id)
            source = db.execute(
                "SELECT raw FROM source_blobs WHERE sha256 = ?", (row["source_sha256"],)
            ).fetchone()
            return bytes(source["raw"])

    def replay(self, dataset_id: str, *, span: TradingWindow | None = None) -> Iterator[Candle]:
        with self.connection() as db:
            yield from self._replay(db, self._dataset(db, dataset_id), span=span)

    def replay_batches(
        self, dataset_id: str, span: TradingWindow, batch_size: int = 1_000
    ) -> Iterator[tuple[Candle, ...]]:
        """Read immutable batches without holding a reader open during consumer writes."""
        yield from self._candle_batches(dataset_id, span, batch_size, require_replayable=True)

    def inspection_batches(
        self, dataset_id: str, span: TradingWindow, batch_size: int = 1_000
    ) -> Iterator[tuple[Candle, ...]]:
        """Read stored rows for comparison, including imports blocked for missing candles."""
        yield from self._candle_batches(dataset_id, span, batch_size, require_replayable=False)

    def _candle_batches(
        self, dataset_id: str, span: TradingWindow, batch_size: int, *, require_replayable: bool
    ) -> Iterator[tuple[Candle, ...]]:
        if batch_size < 1:
            raise ValueError("replay batch size must be positive")
        start = span.start
        while start < span.end:
            with self.connection() as db:
                batch = tuple(
                    self._replay(
                        db,
                        self._dataset(db, dataset_id),
                        span=TradingWindow(start, span.end),
                        limit=batch_size,
                        require_replayable=require_replayable,
                    )
                )
            if not batch:
                break
            yield batch
            if len(batch) < batch_size:
                break
            start = batch[-1].available_at

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

        def run(
            calendar: SessionCalendar, contract: MgcContract, minutes: Iterator[Candle]
        ) -> Iterator[_RecordedEvent]:
            if evidence is not None:
                yield from EvidenceEngine(calendar, contract, timeframe, config, evidence).consume(
                    minutes
                )
                return
            aggregator = CandleAggregator(calendar, contract, (timeframe,))
            engine = StructureEngine(contract, timeframe, config)
            for bar in aggregator.consume(minutes):
                yield from engine.push(bar)

        return self._persist_replay(dataset_id, manifest, run)

    def build_strategy_run(
        self,
        dataset_id: str,
        timeframe: Timeframe,
        structure: StructureConfig,
        strategy: StrategyConfig,
        *,
        evidence: EvidenceConfig = DEFAULT_EVIDENCE_CONFIG,
        context: ContextConfig = DEFAULT_CONTEXT_CONFIG,
        economic: EconomicCalendar | None = None,
        execution: ExecutionConfig | None = None,
    ) -> str:
        manifest: dict[str, object] = {
            "dataset_id": dataset_id,
            "timeframe": timeframe,
            "aggregation_version": AGGREGATION_VERSION,
            "structure_version": STRUCTURE_VERSION,
            "config": asdict(structure),
            "evidence_version": EVIDENCE_VERSION,
            "evidence_config": asdict(evidence),
            "context_version": CONTEXT_VERSION,
            "context_config": asdict(context),
            "strategy_version": STRATEGY_VERSION,
            "strategy_config": asdict(strategy),
            "economic_calendar": economic.as_dict() if economic else None,
            "execution_mode": "paper_research",
        }
        if execution is not None:
            manifest.update(
                backtest_version=BACKTEST_VERSION,
                execution_version=EXECUTION_VERSION,
                performance_version=PERFORMANCE_VERSION,
                execution_config=asdict(execution),
            )

        def run(
            calendar: SessionCalendar, contract: MgcContract, minutes: Iterator[Candle]
        ) -> Iterator[_RecordedEvent]:
            if execution is not None:
                return BacktestReplay(
                    calendar,
                    contract,
                    timeframe,
                    structure,
                    strategy,
                    execution,
                    evidence,
                    context,
                    economic,
                ).consume(minutes)
            return StrategyReplay(
                calendar, contract, timeframe, structure, strategy, evidence, context, economic
            ).consume(minutes)

        return self._persist_replay(dataset_id, manifest, run)

    def _persist_replay(
        self,
        dataset_id: str,
        manifest: dict[str, object],
        run: Callable[[SessionCalendar, MgcContract, Iterator[Candle]], Iterator[_RecordedEvent]],
    ) -> str:
        definition = canonical_json(manifest)
        run_id = sha256(definition.encode()).hexdigest()
        with self.connection() as db:
            if (
                db.execute("PRAGMA user_version").fetchone()[0] >= 2
                and db.execute("SELECT 1 FROM structure_runs WHERE id = ?", (run_id,)).fetchone()
            ):
                return run_id
            dataset = self._dataset(db, dataset_id)
        provenance = json.loads(dataset["provenance"])
        calendar = SessionCalendar.from_dict(provenance["calendar"])
        contract = MgcContract(provenance["contract"])
        span = TradingWindow(
            datetime.fromisoformat(dataset["first_open"]),
            datetime.fromisoformat(dataset["last_close"]),
        )
        minutes = (candle for batch in self.replay_batches(dataset_id, span) for candle in batch)
        with SpooledTemporaryFile(
            mode="w+t", encoding="utf-8", max_size=4 * 1024 * 1024
        ) as journal:
            for event in run(calendar, contract, minutes):
                journal.write(canonical_json(event.as_dict()) + "\n")
            journal.seek(0)
            with self.connection(write=True) as db:
                created = db.execute(
                    "INSERT INTO structure_runs VALUES (?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                    (run_id, dataset_id, definition, datetime.now(UTC).isoformat()),
                )
                if created.rowcount:
                    db.executemany(
                        "INSERT INTO structure_events VALUES (?, ?, ?)",
                        (
                            (run_id, sequence, line.removesuffix("\n"))
                            for sequence, line in enumerate(journal, start=1)
                        ),
                    )
        return run_id

    def describe_structure_run(self, run_id: str) -> dict[str, object]:
        with self.connection() as db:
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

    def describe_backtest(self, run_id: str) -> dict[str, object]:
        description = self.describe_structure_run(run_id)
        if "backtest_version" not in description:
            raise ValueError("run does not contain a paper backtest")
        with self.connection() as db:
            summary = db.execute(
                """SELECT payload FROM structure_events
                   WHERE run_id = ? ORDER BY sequence DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
            if summary is None:
                raise ValueError("backtest has no persisted summary")
            payload = json.loads(summary["payload"])
            if payload["event"] != "paper_backtest_summary":
                raise ValueError("invalid persisted backtest summary")
            return {**description, "summary": payload}

    def structure_events(self, run_id: str) -> Iterator[dict[str, object]]:
        with self.connection() as db:
            run = self._structure_run(db, run_id)
        definition = json.loads(run["definition"])
        provenance = json.loads(run["dataset_provenance"])
        for batch in self.structure_payload_batches(run_id):
            for sequence, payload in batch:
                yield {
                    **payload,
                    "run_id": run_id,
                    "event_id": f"{run_id}/{sequence}",
                    "dataset_id": run["dataset_id"],
                    "data_kind": provenance["data_kind"],
                    "contract": provenance["contract"],
                    "timeframe": definition["timeframe"],
                    "aggregation_version": definition["aggregation_version"],
                    "structure_version": definition["structure_version"],
                }

    def structure_payload_batches(
        self, run_id: str
    ) -> Iterator[list[tuple[int, dict[str, object]]]]:
        """Read raw immutable payloads from a previously resolved research run."""
        after = 0
        while True:
            with self.connection() as db:
                rows = db.execute(
                    """SELECT sequence, payload FROM structure_events
                       WHERE run_id = ? AND sequence > ? ORDER BY sequence LIMIT 1000""",
                    (run_id, after),
                ).fetchall()
            if rows:
                yield [(row["sequence"], json.loads(row["payload"])) for row in rows]
            if len(rows) < 1000:
                return
            after = rows[-1]["sequence"]

    def timed_structure_payloads(
        self, run_id: str, start: datetime | None, through: datetime
    ) -> Iterator[tuple[int, dict[str, object]]]:
        """Read immutable evidence in availability order, retaining sequence on ties."""
        with self.connection() as db:
            indexed = db.execute("PRAGMA user_version").fetchone()[0] >= 11
        if not indexed:
            for batch in self.structure_payload_batches(run_id):
                yield from batch
            return
        stamp = start.isoformat() if start is not None else ""
        after = 0
        end = through.isoformat()
        while True:
            with self.connection() as db:
                rows = db.execute(
                    f"""SELECT sequence, payload, {_STRUCTURE_EVENT_AT} AS available
                        FROM structure_events INDEXED BY structure_event_available
                        WHERE run_id = ? AND {_STRUCTURE_EVENT_AT} >= ?
                          AND ({_STRUCTURE_EVENT_AT}, sequence) > (?, ?)
                          AND {_STRUCTURE_EVENT_AT} <= ?
                        ORDER BY {_STRUCTURE_EVENT_AT}, sequence LIMIT 1000""",
                    (run_id, stamp, stamp, after, end),
                ).fetchall()
            for row in rows:
                yield row["sequence"], json.loads(row["payload"])
            if len(rows) < 1000:
                return
            stamp = rows[-1]["available"]
            after = rows[-1]["sequence"]

    def save_analysis(
        self, kind: str, definition: dict[str, object], result: dict[str, object]
    ) -> str:
        identity = canonical_json({"kind": kind, "definition": definition})
        report_id = sha256(identity.encode()).hexdigest()
        with self.connection(write=True) as db:
            db.execute(
                """INSERT INTO analysis_reports VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO NOTHING""",
                (
                    report_id,
                    kind,
                    canonical_json(definition),
                    canonical_json(result),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return report_id

    def describe_analysis(self, report_id: str) -> dict[str, object]:
        with self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 3:
                row = db.execute(
                    "SELECT * FROM analysis_reports WHERE id = ?", (report_id,)
                ).fetchone()
                if row is not None:
                    return {
                        "report_id": report_id,
                        "kind": row["kind"],
                        "definition": json.loads(row["definition"]),
                        "result": json.loads(row["result"]),
                        "created_at": row["created_at"],
                    }
        raise ValueError(f"unknown analysis report: {report_id}")

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
    def _replay(
        db: sqlite3.Connection,
        dataset: sqlite3.Row,
        *,
        span: TradingWindow | None = None,
        limit: int | None = None,
        require_replayable: bool = True,
    ) -> Iterator[Candle]:
        if require_replayable and not dataset["replayable"]:
            raise ValueError("replay blocked because expected candles are missing")
        contract = MgcContract(json.loads(dataset["provenance"])["contract"])
        query = """SELECT opened_at, open_ticks, high_ticks, low_ticks, close_ticks, volume
                   FROM candles WHERE dataset_id = ?"""
        parameters: tuple[object, ...] = (dataset["id"],)
        if span is not None:
            query += " AND opened_at >= ? AND opened_at < ?"
            parameters += (span.start.isoformat(), span.end.isoformat())
        query += " ORDER BY opened_at"
        if limit is not None:
            query += " LIMIT ?"
            parameters += (limit,)
        rows = db.execute(query, parameters)
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
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
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
        if write and version < 3:
            db.execute(SCHEMA_V3)
        if write and version < 4:
            for statement in SCHEMA_V4:
                db.execute(statement)
        if write and version < 5:
            for statement in SCHEMA_V5:
                db.execute(statement)
        if write and version < 6:
            for statement in SCHEMA_V6:
                db.execute(statement)
        if write and version < 7:
            for statement in SCHEMA_V7:
                db.execute(statement)
        if write and version < 8:
            for statement in SCHEMA_V8:
                db.execute(statement)
        if write and version < 9:
            db.execute(SCHEMA_V9)
        if write and version < 10:
            for statement in SCHEMA_V10:
                db.execute(statement)
        if write and version < 11:
            db.execute(SCHEMA_V11)
            db.execute("PRAGMA user_version = 11")
