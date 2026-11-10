import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from aurum.exchange_calendar import SessionCalendar
from aurum.execution import (
    DEFAULT_EXECUTION_CONFIG,
    EXECUTION_VERSION,
    ExecutionConfig,
    JournalSignal,
    PaperExecution,
)
from aurum.feed_frames import candle_from_dict
from aurum.instruments import MgcContract
from aurum.market_data import canonical_json
from aurum.paper_risk import RiskConfig, TargetMode
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE

FORWARD_VERSION = 1
_BATCH_SIZE = 500


def register_forward(
    store: DatasetStore,
    monitor_id: str,
    started_at: datetime,
    execution: ExecutionConfig = DEFAULT_EXECUTION_CONFIG,
    *,
    synthetic: bool = False,
) -> str:
    if started_at.tzinfo is not UTC:
        raise ValueError("forward registration requires a UTC timestamp")
    if execution.entry_timeout_minutes > 1440:
        raise ValueError("forward entry timeout cannot exceed 1440 minutes")
    with store.connection(write=True) as db:
        row = db.execute(
            "SELECT definition, last_applied FROM monitor_runs WHERE id = ?", (monitor_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown monitor: {monitor_id}")
        monitor = json.loads(row["definition"])
        source = FeedStatus(monitor["monitor_config"]["source_status"])
        required = FeedStatus.SIMULATED if synthetic else FeedStatus.REAL_TIME
        if source is not required:
            raise ValueError(f"forward mode requires a monitor configured as {required}")
        if monitor["monitor_config"]["source"].startswith("replay:") and not synthetic:
            raise ValueError("forward paper cannot use a dataset replay monitor")
        definition = canonical_json(
            {
                "forward_version": FORWARD_VERSION,
                "execution_version": EXECUTION_VERSION,
                "execution_mode": "paper_research",
                "data_kind": "synthetic_forward_verification" if synthetic else "forward_paper",
                "contract": monitor["contract"],
                "monitor_id": monitor_id,
                "source": monitor["monitor_config"]["source"],
                "source_status": source,
                "started_at": started_at.isoformat(),
                "start_after_open": row["last_applied"],
                "execution_config": asdict(execution),
                "entry_basis": "first_minute_open_at_or_after_observation_plus_latency",
                "interruption_policy": "cancel_pending_and_leave_open_outcome_unresolved",
            }
        )
        run_id = sha256(definition.encode()).hexdigest()
        db.execute(
            """INSERT INTO forward_runs (id, monitor_id, definition, cursor)
               VALUES (?, ?, ?, ?) ON CONFLICT (id) DO NOTHING""",
            (run_id, monitor_id, definition, row["last_applied"]),
        )
    return run_id


class ForwardPaper:
    """Consume an immutable monitor prefix and publish paper events with its cursor."""

    def __init__(self, store: DatasetStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id
        with store.connection() as db:
            run = _forward_run(db, run_id)
            self.definition = json.loads(run["definition"])
            self.monitor_id = run["monitor_id"]
            monitor = db.execute(
                "SELECT definition FROM monitor_runs WHERE id = ?", (self.monitor_id,)
            ).fetchone()
            monitor_definition = json.loads(monitor["definition"])
        self._calendar = SessionCalendar.from_dict(monitor_definition["calendar"])
        self._contract = MgcContract(self.definition["contract"])
        self._risk = _risk_config(monitor_definition["strategy_config"]["risk"])
        self._execution = ExecutionConfig(**self.definition["execution_config"])
        self._started = datetime.fromisoformat(self.definition["started_at"])
        self._initial: str | None = self.definition["start_after_open"]
        self._synthetic = self.definition["data_kind"] == "synthetic_forward_verification"
        self._checkpoint = self._initial
        self._engine = self._new_engine()

    def sync(self, max_minutes: int = _BATCH_SIZE) -> dict[str, object]:
        if not 1 <= max_minutes <= _BATCH_SIZE:
            raise ValueError("forward batch size must be between 1 and 500")
        while True:
            with self.store.connection() as db:
                checkpoint = _forward_run(db, self.run_id)["cursor"]
            self._restore(checkpoint)
            try:
                with self.store.connection(write=True) as db:
                    if _forward_run(db, self.run_id)["cursor"] != checkpoint:
                        continue
                    processed, latest = self._commit(db, checkpoint, max_minutes)
                self._checkpoint = latest
            except BaseException:
                self._checkpoint = self._initial
                self._engine = self._new_engine()
                raise
            return {**describe_forward(self.store, self.run_id), "processed_minutes": processed}

    def _new_engine(self) -> PaperExecution:
        return PaperExecution(self._calendar, self._contract, self._risk, self._execution)

    def _restore(self, checkpoint: str | None) -> None:
        if self._checkpoint == checkpoint:
            return
        self._engine = self._new_engine()
        after = self._initial
        while checkpoint is not None and after != checkpoint:
            with self.store.connection() as db:
                batch = self._source(db, after, checkpoint, _BATCH_SIZE)
            if not batch:
                raise ValueError("forward cursor cannot be restored from the monitor journal")
            for minute, signals in batch:
                tuple(self._step(minute, signals))
            after = batch[-1][0]["opened_at"]
        self._checkpoint = checkpoint

    def _commit(
        self, db: sqlite3.Connection, after: str | None, limit: int
    ) -> tuple[int, str | None]:
        batch = self._source(db, after, None, limit)
        if not batch:
            return 0, after
        sequence = db.execute(
            "SELECT coalesce(max(sequence), 0) FROM forward_events WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()[0]
        for minute, signals in batch:
            for event in self._step(minute, signals):
                sequence += 1
                db.execute(
                    "INSERT INTO forward_events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        self.run_id,
                        sequence,
                        minute["opened_at"],
                        event["recorded_at"],
                        event["event"],
                        canonical_json(event),
                    ),
                )
        last = str(batch[-1][0]["opened_at"])
        db.execute(
            """UPDATE forward_runs SET cursor = ?, pending_setup_id = ?, open_setup_id = ?
               WHERE id = ?""",
            (last, self._engine.pending_setup_id, self._engine.open_setup_id, self.run_id),
        )
        return len(batch), last

    def _source(
        self, db: sqlite3.Connection, after: str | None, through: str | None, limit: int
    ) -> list[tuple[sqlite3.Row, list[dict[str, Any]]]]:
        query = """SELECT opened_at, candle, applied_at, execution_eligible FROM monitor_minutes
                   WHERE run_id = ? AND applied = 1"""
        parameters: tuple[object, ...] = (self.monitor_id,)
        if after is not None:
            query += " AND opened_at > ?"
            parameters += (after,)
        if through is not None:
            query += " AND opened_at <= ?"
            parameters += (through,)
        minutes = db.execute(query + " ORDER BY opened_at LIMIT ?", (*parameters, limit)).fetchall()
        if not minutes:
            return []
        first = datetime.fromisoformat(minutes[0]["opened_at"]) + MINUTE
        last = datetime.fromisoformat(minutes[-1]["opened_at"]) + MINUTE
        signals: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in db.execute(
            """SELECT sequence, payload FROM monitor_events
               WHERE run_id = ?
                 AND json_extract(payload, '$.event') = 'paper_setup_transition'
                 AND json_extract(payload, '$.state') = 'paper_signal'
                 AND json_extract(payload, '$.confirmed_at') BETWEEN ? AND ?
               ORDER BY sequence""",
            (self.monitor_id, first.isoformat(), last.isoformat()),
        ):
            payload = json.loads(row["payload"])
            signals[payload["confirmed_at"]].append(
                {**payload, "monitor_sequence": row["sequence"]}
            )
        return [
            (minute, signals[(datetime.fromisoformat(minute["opened_at"]) + MINUTE).isoformat()])
            for minute in minutes
        ]

    def _step(self, row: sqlite3.Row, signals: list[dict[str, Any]]) -> Iterator[dict[str, object]]:
        if row["applied_at"] is None or row["execution_eligible"] is None:
            raise ValueError("forward minute has no durable execution eligibility")
        observed = datetime.fromisoformat(row["applied_at"])
        if observed < self._started:
            return
        if not row["execution_eligible"]:
            for interruption in self._engine.interrupt(observed, "data_continuity_unverified"):
                yield interruption.as_dict()
            return
        candle = candle_from_dict(json.loads(row["candle"]))
        for event in self._engine.advance(candle, observed_at=observed):
            yield event.as_dict()
        for item in signals:
            if not self._synthetic and not item["alert_eligible"]:
                continue
            if datetime.fromisoformat(item["observed_at"]) != observed:
                raise ValueError("forward signal observation does not match its applied minute")
            signal = JournalSignal.from_setup_payload(item)
            yield {
                "event": "paper_forward_signal",
                "recorded_at": observed.isoformat(),
                "confirmed_at": signal.confirmed_at.isoformat(),
                "setup_id": signal.setup_id,
                "monitor_event_id": f"{self.monitor_id}/{item['monitor_sequence']}",
                "direction": signal.direction,
                "session": signal.context.session,
                "regime": signal.context.regime,
                "variant": signal.variant,
                "score": item["score"],
                "plan": signal.plan.as_dict(),
            }
            yield self._engine.submit(signal, observed_at=observed).as_dict()


def _risk_config(raw: dict[str, Any]) -> RiskConfig:
    return RiskConfig(**{**raw, "target_mode": TargetMode(raw["target_mode"])})


def _forward_run(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    if db.execute("PRAGMA user_version").fetchone()[0] >= 7:
        row = db.execute("SELECT * FROM forward_runs WHERE id = ?", (run_id,)).fetchone()
        if isinstance(row, sqlite3.Row):
            return row
    raise ValueError(f"unknown forward run: {run_id}")


def describe_forward(store: DatasetStore, run_id: str) -> dict[str, object]:
    with store.connection() as db:
        db.execute("BEGIN")
        row = _forward_run(db, run_id)
        sequence = db.execute(
            "SELECT coalesce(max(sequence), 0) FROM forward_events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        source = db.execute(
            "SELECT state, last_applied FROM monitor_runs WHERE id = ?", (row["monitor_id"],)
        ).fetchone()
        return {
            **json.loads(row["definition"]),
            "forward_id": run_id,
            "cursor": row["cursor"],
            "pending_setup_id": row["pending_setup_id"],
            "open_setup_id": row["open_setup_id"],
            "event_count": sequence,
            "monitor_state": source["state"],
            "caught_up": row["cursor"] == source["last_applied"],
        }


def list_forward_runs(store: DatasetStore, limit: int = 100) -> list[dict[str, object]]:
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 7:
            return []
        return [
            {
                **json.loads(row["definition"]),
                "forward_id": row["id"],
                "cursor": row["cursor"],
                "pending_setup_id": row["pending_setup_id"],
                "open_setup_id": row["open_setup_id"],
            }
            for row in db.execute(
                """SELECT id, definition, cursor, pending_setup_id, open_setup_id
                   FROM forward_runs ORDER BY json_extract(definition, '$.started_at') DESC, id
                   LIMIT ?""",
                (limit,),
            )
        ]


def forward_events(
    store: DatasetStore, run_id: str, *, after: int = 0, limit: int = 200, latest: bool = False
) -> list[dict[str, object]]:
    with store.connection() as db:
        run = _forward_run(db, run_id)
        definition = json.loads(run["definition"])
        query = """SELECT sequence, minute_open, payload FROM forward_events
                   WHERE run_id = ? AND sequence > ?"""
        query += " ORDER BY sequence DESC LIMIT ?" if latest else " ORDER BY sequence LIMIT ?"
        rows = db.execute(query, (run_id, after, limit)).fetchall()
    if latest:
        rows.reverse()
    return [
        {
            **json.loads(row["payload"]),
            "sequence": row["sequence"],
            "event_id": f"{run_id}/{row['sequence']}",
            "forward_id": run_id,
            "monitor_id": definition["monitor_id"],
            "contract": definition["contract"],
            "data_kind": definition["data_kind"],
            "source_minute_open": row["minute_open"],
        }
        for row in rows
    ]
