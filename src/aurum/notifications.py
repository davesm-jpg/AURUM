import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from aurum.market_data import canonical_json
from aurum.monitoring import HeartbeatStatus, MonitorState, heartbeat_status
from aurum.storage import DatasetStore
from aurum.timestamps import source_timezone


class Channel(StrEnum):
    TELEGRAM = "telegram"
    DISCORD = "discord"


class Severity(StrEnum):
    RESEARCH = "research"
    WARNING = "warning"
    CRITICAL = "critical"


class DeliveryState(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    EXPIRED = "expired"
    FILTERED = "filtered"
    CANCELED = "canceled"


class DeliveryFailure(Exception):
    def __init__(
        self, reason: str, *, retry_seconds: int | None = None, uncertain: bool = False
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_seconds = retry_seconds
        self.uncertain = uncertain


class AlertChannel(Protocol):
    @property
    def channel(self) -> Channel: ...
    @property
    def destination_key(self) -> str: ...
    def send(self, alert: dict[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class DeliveryPreferences:
    timezone: str = "Europe/Prague"
    quiet_start_minute: int | None = None
    quiet_end_minute: int | None = None
    min_score: int = 0
    allowed_sessions: tuple[str, ...] = ()
    severities: tuple[Severity, ...] = tuple(Severity)
    attempts_per_hour: int = 20
    max_attempts: int = 4
    expiry_minutes: int = 15
    lease_seconds: int = 120

    def __post_init__(self) -> None:
        source_timezone(self.timezone)
        start, end = self.quiet_start_minute, self.quiet_end_minute
        if (start is None) != (end is None) or (
            start is not None
            and end is not None
            and (not 0 <= start < 1440 or not 0 <= end < 1440 or start == end)
        ):
            raise ValueError("quiet hours require distinct local start and end minutes")
        if (
            not self.severities
            or not 0 <= self.min_score <= 100
            or min(
                self.attempts_per_hour, self.max_attempts, self.expiry_minutes, self.lease_seconds
            )
            < 1
        ):
            raise ValueError("invalid notification thresholds or limits")

    def quiet(self, at: datetime) -> bool:
        start, end = self.quiet_start_minute, self.quiet_end_minute
        if start is None or end is None:
            return False
        local = at.astimezone(source_timezone(self.timezone))
        minute = local.hour * 60 + local.minute
        return start <= minute < end if start < end else minute >= start or minute < end

    def allows(self, alert: dict[str, object]) -> bool:
        severity = alert.get("severity", Severity.RESEARCH)
        if severity not in self.severities:
            return False
        if alert.get("event") != "paper_research_alert":
            return True
        setup = alert["setup"]
        if not isinstance(setup, dict):
            return False
        score = setup.get("score")
        context = setup.get("context")
        return (
            isinstance(score, dict)
            and score["total"] >= self.min_score
            and (
                not self.allowed_sessions
                or isinstance(context, dict)
                and context["session"] in self.allowed_sessions
            )
        )


DEFAULT_DELIVERY_PREFERENCES = DeliveryPreferences()


@dataclass(frozen=True, slots=True)
class _Claim:
    delivery_id: str
    attempt: int
    payload: dict[str, object]


class DeliveryWorker:
    def __init__(
        self,
        store: DatasetStore,
        monitor_id: str,
        transport: AlertChannel,
        preferences: DeliveryPreferences = DEFAULT_DELIVERY_PREFERENCES,
    ) -> None:
        self.store = store
        self.monitor_id = monitor_id
        self.transport = transport
        self.preferences = preferences

    @property
    def _scope(self) -> tuple[str, Channel, str]:
        return self.monitor_id, self.transport.channel, self.transport.destination_key

    def run_once(
        self,
        limit: int = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> int:
        if limit < 1:
            raise ValueError("delivery batch size must be positive")
        handled = 0
        while handled < limit:
            now = clock()
            _utc(now)
            with self.store.connection(write=True) as db:
                claim = self._claim(db, now)
            if claim is None:
                break
            failure = None
            try:
                self.transport.send(claim.payload)
            except DeliveryFailure as exc:
                failure = exc
            self._finish(claim, max(now, clock()), failure)
            handled += 1
        return handled

    def _claim(self, db: sqlite3.Connection, now: datetime) -> _Claim | None:
        monitor = db.execute(
            "SELECT state, checked_at, definition FROM monitor_runs WHERE id = ?",
            (self.monitor_id,),
        ).fetchone()
        if monitor is None:
            raise ValueError("unknown monitor")
        max_age = json.loads(monitor["definition"])["monitor_config"]["stale_seconds"]
        ready = (
            monitor["state"] == MonitorState.READY
            and heartbeat_status(monitor["checked_at"], now, max_age) is HeartbeatStatus.CURRENT
        )
        self._prepare(db, now)
        if self.preferences.quiet(now):
            return None
        recent = db.execute(
            """SELECT count(*) FROM notification_attempts AS a
               JOIN notification_deliveries AS d ON d.id = a.delivery_id
               WHERE d.monitor_id = ? AND d.channel = ? AND d.destination_key = ?
               AND a.started_at >= ?""",
            (*self._scope, (now - timedelta(hours=1)).isoformat()),
        ).fetchone()[0]
        if recent >= self.preferences.attempts_per_hour:
            return None
        due = db.execute(
            """SELECT d.id, d.attempts, a.payload FROM notification_deliveries AS d
               JOIN monitor_alerts AS a ON a.run_id = d.monitor_id AND a.alert_id = d.alert_id
               WHERE d.monitor_id = ? AND d.channel = ? AND d.destination_key = ?
               AND d.state IN ('pending', 'retry') AND d.due_at <= ?
               ORDER BY d.due_at, a.created_at, d.id""",
            (*self._scope, now.isoformat()),
        )
        for row in due:
            payload = json.loads(row["payload"])
            if payload["event"] == "paper_research_alert" and not ready:
                continue
            attempt = row["attempts"] + 1
            db.execute(
                """UPDATE notification_deliveries
                   SET state = 'sending', attempts = ?, claimed_at = ?
                   WHERE id = ?""",
                (attempt, now.isoformat(), row["id"]),
            )
            db.execute(
                """INSERT INTO notification_attempts (delivery_id, attempt, started_at)
                   VALUES (?, ?, ?)""",
                (row["id"], attempt, now.isoformat()),
            )
            return _Claim(row["id"], attempt, payload)
        return None

    def _prepare(self, db: sqlite3.Connection, now: datetime) -> None:
        scope = self._scope
        stamp = now.isoformat()
        db.execute(
            """UPDATE notification_deliveries AS d SET state = 'canceled'
               WHERE d.monitor_id = ? AND d.channel = ? AND d.destination_key = ?
               AND d.state IN ('pending', 'retry') AND EXISTS (
                   SELECT 1 FROM notification_cancellations AS c
                   WHERE c.monitor_id = d.monitor_id AND c.alert_id = d.alert_id
               )""",
            scope,
        )
        expired_claims = db.execute(
            """SELECT id, attempts FROM notification_deliveries
               WHERE monitor_id = ? AND channel = ? AND destination_key = ?
               AND state = 'sending' AND claimed_at <= ?""",
            (*scope, (now - timedelta(seconds=self.preferences.lease_seconds)).isoformat()),
        ).fetchall()
        for item in expired_claims:
            db.execute(
                """UPDATE notification_deliveries
                   SET state = 'uncertain', last_error = 'lease_expired'
                   WHERE id = ?""",
                (item["id"],),
            )
            _attempt_result(
                db, item["id"], item["attempts"], stamp, DeliveryState.UNCERTAIN, "lease_expired"
            )
        db.execute(
            """UPDATE notification_deliveries SET state = 'expired'
               WHERE monitor_id = ? AND channel = ? AND destination_key = ?
               AND state IN ('pending', 'retry') AND expires_at <= ?""",
            (*scope, stamp),
        )
        threshold = now - timedelta(minutes=self.preferences.expiry_minutes)
        available = db.execute(
            """SELECT a.alert_id, a.payload, a.created_at FROM monitor_alerts AS a
               WHERE a.run_id = ? AND a.created_at > ?
               AND NOT EXISTS (SELECT 1 FROM notification_deliveries AS d
                   WHERE d.monitor_id = a.run_id AND d.alert_id = a.alert_id
                   AND d.channel = ? AND d.destination_key = ?)
               AND NOT EXISTS (SELECT 1 FROM notification_cancellations AS c
                   WHERE c.monitor_id = a.run_id AND c.alert_id = a.alert_id)""",
            (
                self.monitor_id,
                threshold.isoformat(),
                self.transport.channel,
                self.transport.destination_key,
            ),
        )
        for alert in available:
            alert_id = alert["alert_id"]
            identity = sha256(canonical_json((*scope, alert_id)).encode()).hexdigest()
            state = (
                DeliveryState.PENDING
                if self.preferences.allows(json.loads(alert["payload"]))
                else DeliveryState.FILTERED
            )
            expires = datetime.fromisoformat(alert["created_at"]) + timedelta(
                minutes=self.preferences.expiry_minutes
            )
            db.execute(
                """INSERT INTO notification_deliveries
                   (id, monitor_id, alert_id, channel, destination_key, state,
                    attempts, due_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    identity,
                    self.monitor_id,
                    alert_id,
                    self.transport.channel,
                    self.transport.destination_key,
                    state,
                    alert["created_at"],
                    expires.isoformat(),
                ),
            )

    def _finish(self, claim: _Claim, now: datetime, failure: DeliveryFailure | None) -> None:
        _utc(now)
        stamp = now.isoformat()
        state = DeliveryState.SENT
        due = stamp
        error = None
        if failure is not None:
            error = failure.reason
            if failure.uncertain:
                state = DeliveryState.UNCERTAIN
            elif (
                failure.retry_seconds is not None and claim.attempt < self.preferences.max_attempts
            ):
                state = DeliveryState.RETRY
                delay = max(failure.retry_seconds, min(15 * 2 ** (claim.attempt - 1), 900))
                due = (now + timedelta(seconds=delay)).isoformat()
            else:
                state = DeliveryState.FAILED
        with self.store.connection(write=True) as db:
            row = db.execute(
                """UPDATE notification_deliveries SET state = ?, due_at = ?, last_error = ?
                   WHERE id = ? AND attempts = ? AND state = 'sending'""",
                (state, due, error, claim.delivery_id, claim.attempt),
            )
            if row.rowcount:
                _attempt_result(db, claim.delivery_id, claim.attempt, stamp, state, error)


def cancel_delivery(store: DatasetStore, monitor_id: str, alert_id: str, now: datetime) -> None:
    _utc(now)
    with store.connection(write=True) as db:
        if (
            db.execute(
                "SELECT 1 FROM monitor_alerts WHERE run_id = ? AND alert_id = ?",
                (monitor_id, alert_id),
            ).fetchone()
            is None
        ):
            raise ValueError("unknown monitor alert")
        db.execute(
            "INSERT INTO notification_cancellations VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (monitor_id, alert_id, now.isoformat()),
        )
        db.execute(
            """UPDATE notification_deliveries SET state = 'canceled'
               WHERE monitor_id = ? AND alert_id = ? AND state IN ('pending', 'retry')""",
            (monitor_id, alert_id),
        )


def delivery_status(store: DatasetStore, monitor_id: str) -> Iterator[dict[str, object]]:
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 5:
            return
        for item in db.execute(
            """SELECT * FROM notification_deliveries WHERE monitor_id = ?
               ORDER BY due_at, id""",
            (monitor_id,),
        ):
            attempts = db.execute(
                "SELECT * FROM notification_attempts WHERE delivery_id = ? ORDER BY attempt",
                (item["id"],),
            )
            yield {**dict(item), "attempt_log": [dict(attempt) for attempt in attempts]}


def _attempt_result(
    db: sqlite3.Connection,
    delivery_id: str,
    attempt: int,
    stamp: str,
    state: DeliveryState,
    error: str | None,
) -> None:
    db.execute(
        """UPDATE notification_attempts SET finished_at = ?, outcome = ?, error = ?
           WHERE delivery_id = ? AND attempt = ?""",
        (stamp, state, error, delivery_id, attempt),
    )


def _utc(at: datetime) -> None:
    if at.tzinfo is not UTC:
        raise ValueError("delivery clock must be UTC")
