"""Durable, explicitly launched polling of public calendar receipts for review."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from re import fullmatch
from typing import Any

from aurum.economic_archive import EconomicPublisher, EconomicReceipt, capture_official_calendar
from aurum.fed_calendar import FED_MONTH_POLICY_VERSION, fed_month_url
from aurum.market_data import canonical_json
from aurum.storage import DatasetStore

POLLING_VERSION = 1


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(at: datetime) -> None:
    if at.tzinfo is not UTC:
        raise ValueError("economic polling clocks must be aware UTC")


@dataclass(frozen=True, slots=True)
class EconomicPollConfig:
    publisher: EconomicPublisher
    interval_seconds: int = 3600
    retry_seconds: int = 300
    max_retry_seconds: int = 21600
    lease_seconds: int = 120

    def __post_init__(self) -> None:
        if not isinstance(self.publisher, EconomicPublisher):
            raise ValueError("economic polling requires a supported official publisher")
        bounds = (
            (self.interval_seconds, 300, 86400),
            (self.retry_seconds, 60, 86400),
            (self.max_retry_seconds, self.retry_seconds, 86400),
            (self.lease_seconds, 30, 600),
        )
        if any(type(value) is not int or not low <= value <= high for value, low, high in bounds):
            raise ValueError("invalid economic polling cadence, retry or lease bounds")

    def definition(self) -> dict[str, object]:
        return {
            "polling_version": POLLING_VERSION,
            "source_url": self.publisher.url,
            **asdict(self),
            **(
                {"fed_month_policy_version": FED_MONTH_POLICY_VERSION}
                if self.publisher is EconomicPublisher.FED
                else {}
            ),
        }

    @property
    def poller_id(self) -> str:
        return sha256(canonical_json(self.definition()).encode()).hexdigest()

    def backoff(self, failures: int) -> int:
        return min(
            self.max_retry_seconds, self.retry_seconds * (1 << min(max(failures - 1, 0), 16))
        )


def register_economic_poller(
    store: DatasetStore, config: EconomicPollConfig, *, now: datetime | None = None
) -> str:
    now = now if now is not None else _now()
    _utc(now)
    with store.connection(write=True) as db:
        db.execute(
            """INSERT INTO economic_pollers VALUES (?, ?, ?, ?, NULL, NULL, 0)
               ON CONFLICT DO NOTHING""",
            (
                config.poller_id,
                canonical_json(config.definition()),
                now.isoformat(),
                now.isoformat(),
            ),
        )
    return config.poller_id


def _config(row: sqlite3.Row) -> EconomicPollConfig:
    try:
        definition: dict[str, Any] = json.loads(row["definition"])
        config = EconomicPollConfig(
            EconomicPublisher(definition["publisher"]),
            definition["interval_seconds"],
            definition["retry_seconds"],
            definition["max_retry_seconds"],
            definition["lease_seconds"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid economic poller definition") from None
    if definition != config.definition() or config.poller_id != row["id"]:
        raise ValueError("economic poller definition does not match its immutable identity")
    return config


def _run(db: sqlite3.Connection, poller_id: str) -> sqlite3.Row:
    if db.execute("PRAGMA user_version").fetchone()[0] < 16:
        raise ValueError("unknown economic poller")
    row = db.execute("SELECT * FROM economic_pollers WHERE id = ?", (poller_id,)).fetchone()
    if row is None:
        raise ValueError("unknown economic poller")
    assert isinstance(row, sqlite3.Row)
    return row


def poll_economic_calendar(
    store: DatasetStore, poller_id: str, *, clock: Callable[[], datetime] = _now
) -> dict[str, object]:
    now = clock()
    _utc(now)
    with store.connection(write=True) as db:
        row = _run(db, poller_id)
        config = _config(row)
        if now < datetime.fromisoformat(row["created_at"]):
            raise ValueError("economic poll clock precedes registration")
        last = db.execute(
            "SELECT * FROM economic_poll_attempts WHERE poller_id = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (poller_id,),
        ).fetchone()
        attempt = None
        if last is not None and last["outcome"] == "fetching":
            if now >= datetime.fromisoformat(last["lease_until"]):
                _finish(db, row, config, last["sequence"], now, "uncertain", None, "lease_expired")
        elif now >= datetime.fromisoformat(row["next_due_at"]):
            attempt = (last["sequence"] + 1) if last is not None else 1
            db.execute(
                "INSERT INTO economic_poll_attempts "
                "VALUES (?, ?, ?, ?, NULL, 'fetching', NULL, NULL)",
                (
                    poller_id,
                    attempt,
                    now.isoformat(),
                    (now + timedelta(seconds=config.lease_seconds)).isoformat(),
                ),
            )
    if attempt is None:
        return {"attempted": False, **describe_economic_poller(store, poller_id, now=now)}
    receipt = None
    error = None
    try:
        receipt = capture_official_calendar(store, config.publisher, clock=clock)
    except (ValueError, OSError) as exc:
        match = fullmatch(r"official (?:BEA|BLS|FED) calendar returned HTTP ([0-9]{3})", str(exc))
        error = f"http_{match[1]}" if match else "capture_failed"
    finished = clock()
    _utc(finished)
    if finished < now or receipt is not None and not now <= receipt.observed_at <= finished:
        raise ValueError("economic poll completion and receipt clocks must follow the claim")
    with store.connection(write=True) as db:
        _finish(
            db,
            _run(db, poller_id),
            config,
            attempt,
            finished,
            "captured" if receipt else "failed",
            receipt,
            error,
        )
    return {"attempted": True, **describe_economic_poller(store, poller_id, now=finished)}


def _finish(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    config: EconomicPollConfig,
    attempt: int,
    now: datetime,
    outcome: str,
    receipt: EconomicReceipt | None,
    error: str | None,
) -> None:
    active = db.execute(
        "SELECT * FROM economic_poll_attempts WHERE poller_id = ? AND sequence = ?",
        (row["id"], attempt),
    ).fetchone()
    if active is None or active["outcome"] != "fetching":
        return
    if now >= datetime.fromisoformat(active["lease_until"]):
        outcome, error = "uncertain", "lease_expired"
    success = outcome == "captured"
    failures = 0 if success else row["consecutive_failures"] + 1
    due = now + timedelta(seconds=config.interval_seconds if success else config.backoff(failures))
    db.execute(
        """UPDATE economic_poll_attempts SET completed_at = ?, outcome = ?, capture_id = ?,
           error_code = ? WHERE poller_id = ? AND sequence = ?""",
        (
            now.isoformat(),
            outcome,
            receipt.capture_id if receipt else None,
            error,
            row["id"],
            attempt,
        ),
    )
    db.execute(
        """UPDATE economic_pollers SET next_due_at = ?, consecutive_failures = ?,
           last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
           last_capture_id = CASE WHEN ? THEN ? ELSE last_capture_id END WHERE id = ?""",
        (
            due.isoformat(),
            failures,
            success,
            receipt.observed_at.isoformat() if receipt else None,
            success,
            receipt.capture_id if receipt else None,
            row["id"],
        ),
    )


def describe_economic_poller(
    store: DatasetStore, poller_id: str, *, now: datetime | None = None
) -> dict[str, object]:
    now = now if now is not None else _now()
    _utc(now)
    with store.connection() as db:
        db.execute("BEGIN")
        row = _run(db, poller_id)
        config = _config(row)
        last = db.execute(
            "SELECT * FROM economic_poll_attempts WHERE poller_id = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (poller_id,),
        ).fetchone()
        state = "never_captured"
        if last is not None and last["outcome"] == "fetching":
            state = (
                "fetching" if now < datetime.fromisoformat(last["lease_until"]) else "interrupted"
            )
        elif last is not None and last["outcome"] in ("failed", "uncertain"):
            state = str(last["outcome"])
        elif row["last_success_at"] is not None:
            state = (
                "capture_overdue"
                if now >= datetime.fromisoformat(row["next_due_at"])
                else "captured"
            )
        return {
            "poller_id": poller_id,
            "definition": config.definition(),
            "created_at": row["created_at"],
            "next_due_at": row["next_due_at"],
            "last_success_at": row["last_success_at"],
            "last_capture_id": row["last_capture_id"],
            "consecutive_failures": row["consecutive_failures"],
            "state": state,
            "latest_attempt": dict(last) if last else None,
            "review_required": True,
            "automatic_monitor_adoption": False,
        }


def economic_pollers(store: DatasetStore, limit: int = 100) -> list[dict[str, object]]:
    if not 1 <= limit <= 500:
        raise ValueError("economic poller limit must be between 1 and 500")
    if not store.path.is_file():
        return []
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 16:
            return []
        ids = [
            row[0]
            for row in db.execute(
                "SELECT id FROM economic_pollers ORDER BY created_at DESC, id LIMIT ?", (limit,)
            )
        ]
    return [describe_economic_poller(store, reference) for reference in ids]


def economic_poll_attempts(
    store: DatasetStore, poller_id: str, *, after: int = 0, limit: int = 100
) -> list[dict[str, Any]]:
    if after < 0 or not 1 <= limit <= 500:
        raise ValueError("invalid economic poll attempt page")
    with store.connection() as db:
        _config(_run(db, poller_id))
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM economic_poll_attempts WHERE poller_id = ? AND sequence > ? "
                "ORDER BY sequence LIMIT ?",
                (poller_id, after, limit),
            )
        ]


def verify_economic_poller(store: DatasetStore, poller_id: str) -> int:
    """Reconstruct cadence and terminal state from the retained attempt journal."""
    with store.connection() as db:
        db.execute("BEGIN")
        row = _run(db, poller_id)
        config = _config(row)
        due = datetime.fromisoformat(row["created_at"])
        _utc(due)
        failures, count, active = 0, 0, False
        last_success = last_capture = None
        for item in db.execute(
            """SELECT a.*, c.publisher AS receipt_publisher, c.source_url AS receipt_source,
                      c.observed_at AS receipt_observed
               FROM economic_poll_attempts AS a
               LEFT JOIN economic_captures AS c ON c.id = a.capture_id
               WHERE a.poller_id = ? ORDER BY a.sequence""",
            (poller_id,),
        ):
            count += 1
            claimed = datetime.fromisoformat(item["claimed_at"])
            lease = datetime.fromisoformat(item["lease_until"])
            _utc(claimed)
            _utc(lease)
            if (
                active
                or item["sequence"] != count
                or claimed < due
                or lease != claimed + timedelta(seconds=config.lease_seconds)
            ):
                raise ValueError("economic poll attempt chronology or lease is inconsistent")
            outcome = item["outcome"]
            if outcome == "fetching":
                if any(
                    item[key] is not None for key in ("completed_at", "capture_id", "error_code")
                ):
                    raise ValueError("unfinished economic poll has terminal metadata")
                active = True
                continue
            if item["completed_at"] is None:
                raise ValueError("completed economic poll is missing its completion clock")
            completed = datetime.fromisoformat(item["completed_at"])
            _utc(completed)
            if completed < claimed or (outcome == "uncertain") != (completed >= lease):
                raise ValueError("economic poll completion and lease disagree")
            observed = None
            if item["capture_id"] is not None:
                if item[
                    "receipt_publisher"
                ] != config.publisher or not config.publisher.accepts_source_url(
                    item["receipt_source"]
                ):
                    raise ValueError("economic poll receipt disagrees with its official publisher")
                observed = datetime.fromisoformat(item["receipt_observed"])
                _utc(observed)
                if not claimed <= observed <= completed:
                    raise ValueError("economic poll receipt lies outside its observation interval")
                if config.publisher is EconomicPublisher.FED and item["receipt_source"] not in {
                    fed_month_url(claimed),
                    fed_month_url(observed),
                }:
                    raise ValueError("Fed poll receipt disagrees with its current-month policy")
            if outcome == "captured":
                if observed is None or item["error_code"] is not None:
                    raise ValueError("successful economic poll has inconsistent receipt metadata")
                last_capture, last_success, failures = item["capture_id"], observed.isoformat(), 0
                due = completed + timedelta(seconds=config.interval_seconds)
            else:
                if outcome == "uncertain":
                    valid = item["error_code"] == "lease_expired"
                else:
                    valid = item["capture_id"] is None and (
                        item["error_code"] == "capture_failed"
                        or fullmatch(r"http_[1-5][0-9]{2}", item["error_code"] or "") is not None
                    )
                if not valid:
                    raise ValueError("failed economic poll has inconsistent outcome metadata")
                failures += 1
                due = completed + timedelta(seconds=config.backoff(failures))
        if (
            row["next_due_at"] != due.isoformat()
            or row["last_capture_id"] != last_capture
            or row["last_success_at"] != last_success
            or row["consecutive_failures"] != failures
        ):
            raise ValueError("economic poller state does not match its attempt journal")
    return count
