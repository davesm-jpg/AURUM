"""Reviewed economic snapshots activated on an immutable monitor timeline."""

import json
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from itertools import pairwise

from aurum.economic_events import EconomicCalendar, EconomicSourceKind
from aurum.market_data import canonical_json
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE

ECONOMIC_UPDATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class EconomicTimeline:
    baseline: EconomicCalendar | None
    updates: tuple[tuple[datetime, str], ...]
    _resolved: dict[str, EconomicCalendar | None] = field(default_factory=dict, compare=False)

    def at(
        self, db: sqlite3.Connection, when: datetime
    ) -> tuple[EconomicCalendar | None, str | None]:
        index = bisect_right(self.updates, when, key=lambda item: item[0]) - 1
        if index < 0:
            return self.baseline, None
        update_id = self.updates[index][1]
        if update_id in self._resolved:
            return self._resolved[update_id], update_id
        row = db.execute(
            "SELECT * FROM monitor_economic_updates WHERE id = ?", (update_id,)
        ).fetchone()
        if row is None:
            raise ValueError("economic activation record is missing")
        self._resolved[update_id] = _verified_calendar(row)
        return self._resolved[update_id], update_id


@lru_cache(maxsize=16)
def _calendar(payload: str) -> EconomicCalendar | None:
    return None if payload == "null" else EconomicCalendar.from_json(payload)


def _verified_calendar(row: sqlite3.Row) -> EconomicCalendar | None:
    snapshot = _calendar(row["calendar"])
    definition = {
        "economic_update_version": row["rule_version"],
        "monitor_id": row["monitor_id"],
        "activated_at": row["activated_at"],
        "calendar": snapshot.as_dict() if snapshot is not None else None,
        "reason": row["reason"],
    }
    if (
        row["rule_version"] != ECONOMIC_UPDATE_VERSION
        or sha256(canonical_json(definition).encode()).hexdigest() != row["id"]
    ):
        raise ValueError("economic activation record does not match its identity")
    return snapshot


def economic_timeline(
    db: sqlite3.Connection, monitor_id: str, baseline: EconomicCalendar | None
) -> EconomicTimeline:
    if db.execute("PRAGMA user_version").fetchone()[0] < 15:
        return EconomicTimeline(baseline, ())
    rows = db.execute(
        "SELECT activated_at, id FROM monitor_economic_updates "
        "WHERE monitor_id = ? ORDER BY sequence",
        (monitor_id,),
    )
    updates = tuple((datetime.fromisoformat(row["activated_at"]), row["id"]) for row in rows)
    if any(at.tzinfo is not UTC for at, _ in updates) or any(
        older[0] >= newer[0] for older, newer in pairwise(updates)
    ):
        raise ValueError("economic activation history must have strictly increasing UTC clocks")
    return EconomicTimeline(baseline, updates)


def adopt_economic_snapshot(
    store: DatasetStore,
    monitor_id: str,
    snapshot: EconomicCalendar | None,
    *,
    activated_at: datetime,
    reason: str,
) -> str:
    if activated_at.tzinfo is not UTC:
        raise ValueError("economic activation time must be UTC")
    reason = reason.strip()
    if not 1 <= len(reason) <= 1000:
        raise ValueError("economic activation requires a review reason of at most 1000 characters")
    if snapshot is not None:
        _reviewed(snapshot, activated_at)
    definition = {
        "economic_update_version": ECONOMIC_UPDATE_VERSION,
        "monitor_id": monitor_id,
        "activated_at": activated_at.isoformat(),
        "calendar": snapshot.as_dict() if snapshot is not None else None,
        "reason": reason,
    }
    update_id = sha256(canonical_json(definition).encode()).hexdigest()
    with store.connection(write=True) as db:
        run = db.execute("SELECT * FROM monitor_runs WHERE id = ?", (monitor_id,)).fetchone()
        if run is None:
            raise ValueError(f"unknown monitor: {monitor_id}")
        existing = db.execute(
            "SELECT * FROM monitor_economic_updates WHERE id = ?", (update_id,)
        ).fetchone()
        if existing is not None:
            _verified_calendar(existing)
            return update_id
        monitor = json.loads(run["definition"])
        if (
            snapshot is not None
            and snapshot.data_kind is EconomicSourceKind.SYNTHETIC
            and monitor["monitor_config"]["source_status"] != FeedStatus.SIMULATED
        ):
            raise ValueError("synthetic economic updates require a simulated monitor")
        last = db.execute(
            "SELECT sequence, activated_at FROM monitor_economic_updates WHERE monitor_id = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (monitor_id,),
        ).fetchone()
        observed = db.execute(
            "SELECT max(applied_at) FROM monitor_minutes WHERE run_id = ? AND applied = 1",
            (monitor_id,),
        ).fetchone()[0]
        prior = [datetime.fromisoformat(observed)] if observed is not None else []
        if run["last_applied"] is not None:
            prior.append(datetime.fromisoformat(run["last_applied"]) + MINUTE)
        if last is not None:
            prior.append(datetime.fromisoformat(last["activated_at"]))
        if prior and activated_at <= max(prior):
            raise ValueError("economic activation must follow all processed and activation clocks")
        db.execute(
            "INSERT INTO monitor_economic_updates VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                monitor_id,
                last["sequence"] + 1 if last is not None else 1,
                update_id,
                activated_at.isoformat(),
                canonical_json(definition["calendar"]),
                reason,
                ECONOMIC_UPDATE_VERSION,
            ),
        )
    return update_id


def _reviewed(snapshot: EconomicCalendar, activated_at: datetime) -> None:
    if snapshot.data_kind is None:
        raise ValueError("reviewed economic updates require an explicit source classification")
    if len(snapshot.events) > 5000:
        raise ValueError("reviewed updates support at most 5000 economic announcements")
    known = [snapshot.coverage_known_at]
    known.extend(capture.observed_at for capture in snapshot.captures)
    for publication in snapshot.publications:
        known.append(publication.coverage_known_at)
        known.extend(capture.observed_at for capture in publication.captures)
    for event in snapshot.events:
        known.append(event.published_at)
        if event.actual_known_at is not None:
            known.append(event.actual_known_at)
    if max(known) > activated_at:
        raise ValueError("economic activation precedes supplied publication knowledge")


def economic_updates(store: DatasetStore, monitor_id: str) -> list[dict[str, object]]:
    with store.connection() as db:
        if db.execute("SELECT 1 FROM monitor_runs WHERE id = ?", (monitor_id,)).fetchone() is None:
            raise ValueError(f"unknown monitor: {monitor_id}")
        if db.execute("PRAGMA user_version").fetchone()[0] < 15:
            return []
        return [
            {
                "update_id": row["id"],
                "sequence": row["sequence"],
                "activated_at": row["activated_at"],
                "calendar": json.loads(row["calendar"]),
                "reason": row["reason"],
                "economic_update_version": row["rule_version"],
            }
            for row in db.execute(
                "SELECT * FROM monitor_economic_updates WHERE monitor_id = ? ORDER BY sequence",
                (monitor_id,),
            )
        ]
