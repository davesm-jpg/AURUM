import json
import sqlite3
from base64 import b64encode
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from aurum.candles import AGGREGATION_VERSION, Timeframe
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EVIDENCE_VERSION, EvidenceConfig
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.feed_frames import FeedFrame, candle_from_dict
from aurum.instruments import MgcContract
from aurum.market_context import CONTEXT_VERSION
from aurum.market_data import Candle, canonical_json
from aurum.market_structure import STRUCTURE_VERSION, StructureConfig
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.strategy import (
    STRATEGY_VERSION,
    SetupEvent,
    SetupState,
    StrategyConfig,
    StrategyReplay,
    StrategyStep,
)
from aurum.timestamps import MINUTE, source_timezone

MONITOR_VERSION = 1


class MonitorState(StrEnum):
    WARMING = "warming"
    READY = "ready"
    RECOVERING = "recovering_gap"
    STALE = "stale"
    DISCONNECTED = "disconnected"
    CLOSED = "market_closed"
    UNAVAILABLE = "calendar_unavailable"
    INVALID = "integrity_fault"


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    source: str
    source_status: FeedStatus
    stale_seconds: int = 90
    pending_limit: int = 10_000
    timeframe: Timeframe = Timeframe.FIVE_MINUTES
    source_origin: str | None = None
    source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip() or self.source_status is FeedStatus.UNAVAILABLE:
            raise ValueError("monitor requires a named, classified feed")
        if min(self.stale_seconds, self.pending_limit) < 1:
            raise ValueError("monitor staleness and pending limits must be positive")


class MarketMonitor:
    """Persistent, one-contract research monitor with a fail-closed alert gate."""

    def __init__(
        self,
        store: DatasetStore,
        calendar: SessionCalendar,
        contract: MgcContract,
        config: MonitorConfig,
        structure: StructureConfig,
        strategy: StrategyConfig,
        evidence: EvidenceConfig = DEFAULT_EVIDENCE_CONFIG,
    ) -> None:
        if calendar.sessions is None:
            raise ValueError("monitoring requires reviewed dated sessions")
        self.store = store
        self.calendar = calendar
        self.contract = contract
        self.config = config
        self._structure = structure
        self._strategy_config = strategy
        self._evidence = evidence
        self._checkpoint: str | None = None
        self._engine = self._new_engine()
        definition = canonical_json(
            {
                "monitor_version": MONITOR_VERSION,
                "aggregation_version": AGGREGATION_VERSION,
                "structure_version": STRUCTURE_VERSION,
                "evidence_version": EVIDENCE_VERSION,
                "context_version": CONTEXT_VERSION,
                "strategy_version": STRATEGY_VERSION,
                "contract": contract.code,
                "calendar": calendar.as_dict(),
                "monitor_config": asdict(config),
                "structure_config": asdict(structure),
                "strategy_config": asdict(strategy),
                "evidence_config": asdict(evidence),
                "execution_mode": "paper_research",
            }
        )
        self.monitor_id = sha256(definition.encode()).hexdigest()
        expected = calendar.windows[0].start.isoformat() if calendar.windows else None
        with store.connection(write=True) as db:
            db.execute(
                """INSERT INTO monitor_runs
                   (id, definition, state, connected, next_open)
                   VALUES (?, ?, ?, 0, ?) ON CONFLICT (id) DO NOTHING""",
                (self.monitor_id, definition, MonitorState.DISCONNECTED, expected),
            )
            self._restore(db, _run(db, self.monitor_id)["last_applied"])

    def set_connection(self, connected: bool, now: datetime) -> dict[str, object]:
        _utc(now)
        with self.store.connection(write=True) as db:
            row = _run(db, self.monitor_id)
            if bool(row["connected"]) != connected:
                db.execute(
                    "UPDATE monitor_runs SET connected = ? WHERE id = ?",
                    (connected, self.monitor_id),
                )
                self._record(
                    db,
                    {
                        "event": "feed_connected" if connected else "feed_disconnected",
                        "at": now.isoformat(),
                    },
                )
            return self._refresh(db, now)

    def refresh(self, now: datetime) -> dict[str, object]:
        _utc(now)
        with self.store.connection(write=True) as db:
            return self._refresh(db, now)

    def fault(self, now: datetime, reason: str) -> dict[str, object]:
        _utc(now)
        with self.store.connection(write=True) as db:
            self._latch(db, now, reason)
            return self._refresh(db, now)

    def note(self, now: datetime, event: str, reason: str) -> None:
        _utc(now)
        with self.store.connection(write=True) as db:
            self._record(db, {"event": event, "at": now.isoformat(), "reason": reason})

    def gap(self) -> TradingWindow | None:
        with self.store.connection() as db:
            row = _run(db, self.monitor_id)
            if row["fault"] is not None or row["next_open"] is None:
                return None
            future = db.execute(
                """SELECT min(opened_at) FROM monitor_minutes
                   WHERE run_id = ? AND applied = 0""",
                (self.monitor_id,),
            ).fetchone()[0]
            if future is None or future <= row["next_open"]:
                return None
            return TradingWindow(
                datetime.fromisoformat(row["next_open"]), datetime.fromisoformat(future)
            )

    def ingest(self, frame: FeedFrame) -> dict[str, object]:
        candle = frame.candle
        if candle.contract != self.contract or not self.calendar.permits(candle.opened_at):
            raise ValueError("feed candle is outside the configured contract or schedule")
        if frame.source_status is not self.config.source_status and not (
            frame.source_status is FeedStatus.HISTORICAL
            and self.config.source_status in (FeedStatus.REAL_TIME, FeedStatus.DELAYED)
        ):
            raise ValueError("feed candle status is incompatible with monitor configuration")
        try:
            with self.store.connection(write=True) as db:
                return self._ingest(db, frame)
        except BaseException:
            self._checkpoint = None
            self._engine = self._new_engine()
            raise

    def _ingest(self, db: sqlite3.Connection, frame: FeedFrame) -> dict[str, object]:
        row = _run(db, self.monitor_id)
        self._restore(db, row["last_applied"])
        now = frame.received_at
        opened = frame.candle.opened_at.isoformat()
        normalized = canonical_json(frame.candle.as_dict())
        db.execute(
            """UPDATE monitor_runs SET last_received = max(coalesce(last_received, ''), ?)
               WHERE id = ?""",
            (now.isoformat(), self.monitor_id),
        )
        previous = db.execute(
            "SELECT candle FROM monitor_minutes WHERE run_id = ? AND opened_at = ?",
            (self.monitor_id, opened),
        ).fetchone()
        if previous is not None:
            if previous["candle"] != normalized:
                self._latch(
                    db,
                    now,
                    "conflicting_duplicate",
                    {
                        "opened_at": opened,
                        "conflicting_raw_base64": b64encode(frame.raw).decode(),
                    },
                )
            return self._refresh(db, now)
        if row["fault"] is not None:
            return self._refresh(db, now)
        pending = _pending_count(db, self.monitor_id)
        if opened != row["next_open"] and pending >= self.config.pending_limit:
            self._latch(db, now, "pending_buffer_limit")
            return self._refresh(db, now)
        db.execute(
            """INSERT INTO monitor_minutes
               (run_id, opened_at, candle, raw, received_at, source_status, applied)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (self.monitor_id, opened, normalized, frame.raw, now.isoformat(), frame.source_status),
        )
        expected = row["next_open"]
        last_applied = row["last_applied"]
        observed = max(now.isoformat(), row["last_received"] or "")
        direct = (
            pending == 0
            and expected == opened
            and row["connected"]
            and (row["checked_at"] is None or now >= datetime.fromisoformat(row["checked_at"]))
            and frame.source_status is self.config.source_status
            and frame.source_status in (FeedStatus.REAL_TIME, FeedStatus.SIMULATED)
            and (now - frame.candle.available_at).total_seconds() <= self.config.stale_seconds
        )
        fresh_frame = (
            direct
            and frame.source_status is FeedStatus.REAL_TIME
            and self.calendar.permits(now.replace(second=0, microsecond=0))
        )
        while expected is not None:
            available = db.execute(
                """SELECT candle, source_status, received_at FROM monitor_minutes
                   WHERE run_id = ? AND opened_at = ? AND applied = 0""",
                (self.monitor_id, expected),
            ).fetchone()
            if available is None:
                break
            minute = candle_from_dict(json.loads(available["candle"]))
            step = self._engine.step(minute)
            db.execute(
                """UPDATE monitor_minutes SET applied = 1, applied_at = ?, execution_eligible = ?
                   WHERE run_id = ? AND opened_at = ?""",
                (observed, int(direct and expected == opened), self.monitor_id, expected),
            )
            if step is not None:
                self._record_market(db, minute, step)
            for event in step.setups if step is not None else ():
                alert_eligible = (
                    fresh_frame and expected == opened and event.state is SetupState.PAPER_SIGNAL
                )
                self._record(
                    db,
                    {
                        **event.as_dict(),
                        "source_status": available["source_status"],
                        "source_received_at": available["received_at"],
                        "observed_at": observed,
                        "alert_eligible": alert_eligible,
                    },
                )
                if alert_eligible:
                    self._alert(db, event, now)
            last_applied = expected
            expected = self._next_open(minute.opened_at)
        db.execute(
            "UPDATE monitor_runs SET next_open = ?, last_applied = ? WHERE id = ?",
            (expected, last_applied, self.monitor_id),
        )
        self._checkpoint = last_applied
        return self._refresh(db, now)

    def _restore(self, db: sqlite3.Connection, checkpoint: str | None) -> None:
        if checkpoint == self._checkpoint:
            return
        self._engine = self._new_engine()
        for row in db.execute(
            """SELECT candle FROM monitor_minutes
               WHERE run_id = ? AND applied = 1 ORDER BY opened_at""",
            (self.monitor_id,),
        ):
            self._engine.push(candle_from_dict(json.loads(row["candle"])))
        self._checkpoint = checkpoint

    def _new_engine(self) -> StrategyReplay:
        return StrategyReplay(
            self.calendar,
            self.contract,
            self.config.timeframe,
            self._structure,
            self._strategy_config,
            self._evidence,
        )

    def _next_open(self, opened: datetime) -> str | None:
        windows = self.calendar.windows
        index = bisect_right(windows, opened, key=lambda span: span.start) - 1
        following = opened + MINUTE
        if following < windows[index].end:
            return following.isoformat()
        return windows[index + 1].start.isoformat() if index + 1 < len(windows) else None

    def _refresh(self, db: sqlite3.Connection, now: datetime) -> dict[str, object]:
        row = _run(db, self.monitor_id)
        if row["checked_at"] is not None:
            now = max(now, datetime.fromisoformat(row["checked_at"]))
        pending = _pending_count(db, self.monitor_id)
        state = self._state(row, now, pending)
        if row["state"] != state:
            self._record(
                db,
                {
                    "event": "monitor_health_changed",
                    "at": now.isoformat(),
                    "previous": row["state"],
                    "state": state,
                    "next_expected_open": row["next_open"],
                    "pending_minutes": pending,
                },
            )
            if self.config.source_status is FeedStatus.REAL_TIME and state in (
                MonitorState.RECOVERING,
                MonitorState.STALE,
                MonitorState.DISCONNECTED,
                MonitorState.INVALID,
            ):
                self._health_alert(db, state, now)
        db.execute(
            "UPDATE monitor_runs SET state = ?, checked_at = ? WHERE id = ?",
            (state, now.isoformat(), self.monitor_id),
        )
        return _status(_run(db, self.monitor_id), pending)

    def _state(self, row: sqlite3.Row, now: datetime, pending: int) -> MonitorState:
        if row["fault"] is not None:
            return MonitorState.INVALID
        if not self.calendar.coverage.start <= now < self.calendar.coverage.end:
            return MonitorState.UNAVAILABLE
        if pending:
            return MonitorState.RECOVERING
        if not self.calendar.permits(now.replace(second=0, microsecond=0)):
            return MonitorState.CLOSED
        if not row["connected"]:
            return MonitorState.DISCONNECTED
        expected = row["next_open"]
        if expected is not None:
            deadline = datetime.fromisoformat(expected) + MINUTE
            if (now - deadline).total_seconds() > self.config.stale_seconds:
                return MonitorState.STALE
        return MonitorState.READY if row["last_applied"] else MonitorState.WARMING

    def _latch(
        self,
        db: sqlite3.Connection,
        now: datetime,
        reason: str,
        details: dict[str, object] | None = None,
    ) -> None:
        db.execute(
            "UPDATE monitor_runs SET fault = coalesce(fault, ?) WHERE id = ?",
            (reason, self.monitor_id),
        )
        self._record(
            db,
            {
                "event": "feed_integrity_fault",
                "at": now.isoformat(),
                "reason": reason,
                "details": details or {},
            },
        )

    def _record(self, db: sqlite3.Connection, payload: dict[str, object]) -> None:
        db.execute(
            """INSERT INTO monitor_events
               SELECT ?, coalesce(max(sequence), 0) + 1, ?
               FROM monitor_events WHERE run_id = ?""",
            (self.monitor_id, canonical_json(payload), self.monitor_id),
        )

    def _record_market(self, db: sqlite3.Connection, minute: Candle, step: StrategyStep) -> None:
        sequence = db.execute(
            "SELECT coalesce(max(sequence), 0) FROM monitor_market_events WHERE run_id = ?",
            (self.monitor_id,),
        ).fetchone()[0]
        payloads = [event.as_dict() for event in step.evidence]
        payloads.append(
            {
                "event": "market_context_observed",
                "confirmed_at": minute.available_at.isoformat(),
                "context": step.context.as_dict(),
            }
        )
        db.executemany(
            "INSERT INTO monitor_market_events VALUES (?, ?, ?, ?, ?)",
            (
                (
                    self.monitor_id,
                    number,
                    minute.opened_at.isoformat(),
                    payload["event"],
                    canonical_json(payload),
                )
                for number, payload in enumerate(payloads, start=sequence + 1)
            ),
        )

    def _alert(self, db: sqlite3.Connection, event: SetupEvent, now: datetime) -> None:
        alert_id = f"{self.monitor_id}/{event.setup_id}"
        payload = {
            "alert_id": alert_id,
            "event": "paper_research_alert",
            "severity": "research",
            "execution_mode": "paper_research",
            "contract": self.contract.code,
            "source": self.config.source,
            "data_status": FeedStatus.REAL_TIME,
            "feed_age_seconds": (now - event.confirmed_at).total_seconds(),
            "new_york_at": event.confirmed_at.astimezone(
                source_timezone("America/New_York")
            ).isoformat(),
            "prague_at": event.confirmed_at.astimezone(
                source_timezone("Europe/Prague")
            ).isoformat(),
            "setup": event.as_dict(),
        }
        self._queue_alert(db, alert_id, payload, now)

    def _health_alert(self, db: sqlite3.Connection, state: MonitorState, now: datetime) -> None:
        alert_id = f"{self.monitor_id}/health/{state}/{now.isoformat()}"
        self._queue_alert(
            db,
            alert_id,
            {
                "alert_id": alert_id,
                "event": "data_quality_warning",
                "severity": "critical" if state is MonitorState.INVALID else "warning",
                "contract": self.contract.code,
                "source": self.config.source,
                "state": state,
                "execution_mode": "paper_research",
            },
            now,
        )

    def _queue_alert(
        self, db: sqlite3.Connection, alert_id: str, payload: dict[str, object], now: datetime
    ) -> None:
        db.execute(
            "INSERT INTO monitor_alerts VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (self.monitor_id, alert_id, canonical_json(payload), now.isoformat()),
        )


def describe_monitor(store: DatasetStore, monitor_id: str) -> dict[str, object]:
    with store.connection() as db:
        db.execute("BEGIN")
        return _status(_run(db, monitor_id), _pending_count(db, monitor_id))


def applied_minute_batches(
    store: DatasetStore, monitor_id: str, span: TradingWindow
) -> Iterator[list[tuple[Candle, FeedStatus]]]:
    """Read immutable applied minutes up to a previously observed journal checkpoint."""
    start = span.start.isoformat()
    end = span.end.isoformat()
    while True:
        with store.connection() as db:
            rows = db.execute(
                """SELECT opened_at, candle, source_status FROM monitor_minutes
                   WHERE run_id = ? AND applied = 1 AND opened_at >= ? AND opened_at < ?
                   ORDER BY opened_at LIMIT 1000""",
                (monitor_id, start, end),
            ).fetchall()
        if rows:
            yield [
                (candle_from_dict(json.loads(row["candle"])), FeedStatus(row["source_status"]))
                for row in rows
            ]
        if len(rows) < 1000:
            return
        start = (datetime.fromisoformat(rows[-1]["opened_at"]) + MINUTE).isoformat()


def market_events_page(
    store: DatasetStore,
    monitor_id: str,
    *,
    after: int = 0,
    limit: int = 200,
    latest: bool = False,
    through_open: str | None = None,
    observed_through: datetime | None = None,
    kind: str | None = None,
) -> list[dict[str, object]]:
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 6:
            return []
        query = """SELECT e.sequence, e.payload, m.source_status, m.received_at, m.applied_at
                   FROM monitor_market_events AS e
                   JOIN monitor_minutes AS m
                     ON e.run_id = m.run_id AND e.minute_open = m.opened_at
                   WHERE e.run_id = ? AND e.sequence > ?"""
        parameters: tuple[object, ...] = (monitor_id, after)
        if through_open is not None:
            query += " AND e.minute_open <= ?"
            parameters += (through_open,)
        if observed_through is not None:
            query += " AND m.applied_at <= ?"
            parameters += (observed_through.isoformat(),)
        if kind is not None:
            query += " AND e.kind = ?"
            parameters += (kind,)
        query += " ORDER BY e.sequence DESC LIMIT ?" if latest else " ORDER BY e.sequence LIMIT ?"
        rows = db.execute(query, (*parameters, limit)).fetchall()
    if latest:
        rows.reverse()
    return [
        {
            **json.loads(row["payload"]),
            "sequence": row["sequence"],
            "event_id": f"{monitor_id}/market/{row['sequence']}",
            "source_status": row["source_status"],
            "source_received_at": row["received_at"],
            "observed_at": row["applied_at"],
        }
        for row in rows
    ]


def market_evidence_started_at(
    store: DatasetStore, monitor_id: str, through_open: str
) -> str | None:
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 6:
            return None
        row = db.execute(
            """SELECT applied_at FROM monitor_minutes
               WHERE run_id = ? AND applied_at IS NOT NULL AND opened_at <= ?
               ORDER BY opened_at LIMIT 1""",
            (monitor_id, through_open),
        ).fetchone()
    return str(row["applied_at"]) if row is not None else None


def monitor_events(store: DatasetStore, monitor_id: str) -> Iterator[dict[str, object]]:
    with store.connection() as db:
        row = _run(db, monitor_id)
        source = json.loads(row["definition"])["monitor_config"]["source_status"]
        for event in db.execute(
            "SELECT sequence, payload FROM monitor_events WHERE run_id = ? ORDER BY sequence",
            (monitor_id,),
        ):
            yield {
                "source_status": source,
                **json.loads(event["payload"]),
                "monitor_id": monitor_id,
                "event_id": f"{monitor_id}/{event['sequence']}",
            }


def monitor_alerts(store: DatasetStore, monitor_id: str) -> Iterator[dict[str, object]]:
    with store.connection() as db:
        _run(db, monitor_id)
        for alert in db.execute(
            """SELECT payload, created_at FROM monitor_alerts
               WHERE run_id = ? ORDER BY created_at, alert_id""",
            (monitor_id,),
        ):
            yield {**json.loads(alert["payload"]), "created_at": alert["created_at"]}


def _run(db: sqlite3.Connection, monitor_id: str) -> sqlite3.Row:
    if db.execute("PRAGMA user_version").fetchone()[0] >= 4:
        row = db.execute("SELECT * FROM monitor_runs WHERE id = ?", (monitor_id,)).fetchone()
        if isinstance(row, sqlite3.Row):
            return row
    raise ValueError(f"unknown monitor: {monitor_id}")


def _pending_count(db: sqlite3.Connection, monitor_id: str) -> int:
    return int(
        db.execute(
            "SELECT count(*) FROM monitor_minutes WHERE run_id = ? AND applied = 0", (monitor_id,)
        ).fetchone()[0]
    )


def _status(row: sqlite3.Row, pending: int) -> dict[str, object]:
    definition = json.loads(row["definition"])
    source_status = definition["monitor_config"]["source_status"]
    return {
        "monitor_id": row["id"],
        "definition": definition,
        "state": row["state"],
        "connected": bool(row["connected"]),
        "next_expected_open": row["next_open"],
        "last_applied_open": row["last_applied"],
        "last_received": row["last_received"],
        "checked_at": row["checked_at"],
        "pending_minutes": pending,
        "fault": row["fault"],
        "alerts_suspended": row["state"] != MonitorState.READY
        or source_status != FeedStatus.REAL_TIME,
    }


def _utc(now: datetime) -> None:
    if now.tzinfo is not UTC:
        raise ValueError("monitor clock must be UTC")
