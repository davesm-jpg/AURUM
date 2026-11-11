"""Reconstruct monitor input candles offline from their retained frame evidence."""

import json
from bisect import bisect_right
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from aurum.exchange_calendar import SessionCalendar
from aurum.feed_frames import FeedFrame, candle_from_dict
from aurum.instruments import MgcContract
from aurum.market_data import Candle, canonical_json
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE

MONITOR_AUDIT_VERSION = 2
MAX_FRAME_BYTES = 64 * 1024


class MonitorInputIntegrityError(ValueError):
    """Preserved frame evidence disagrees with the saved research input."""


def preserved_monitor_candle(
    row: Mapping[str, Any],
    definition: dict[str, Any],
) -> Candle:
    try:
        raw = row["raw"]
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FRAME_BYTES:
            raise ValueError("invalid preserved monitor frame size")
        received = datetime.fromisoformat(row["received_at"])
        if received.tzinfo is not UTC:
            raise ValueError("monitor receipt must be aware UTC")
        mode = FeedStatus(row["source_status"])
        declared = FeedStatus(definition["monitor_config"]["source_status"])
        if mode is not declared and not (
            mode is FeedStatus.HISTORICAL and declared in (FeedStatus.REAL_TIME, FeedStatus.DELAYED)
        ):
            raise ValueError("monitor source classification differs from its manifest")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("preserved monitor frame must be a JSON object")
        if payload.get("event") == "candle_closed":
            frame = FeedFrame.from_message(raw, mode, received)
        elif "adapter" in payload:
            from aurum.databento_live import frame_from_preserved_native

            frame, fingerprint = frame_from_preserved_native(payload, mode, received)
            if (
                definition["monitor_config"].get("source_fingerprint") != fingerprint
                or definition["monitor_config"].get("source_origin") != "databento://GLBX.MDP3"
            ):
                raise ValueError("preserved native source differs from its monitor identity")
        else:
            candle = candle_from_dict(payload)
            if payload != candle.as_dict():
                raise ValueError("preserved replay candle metadata differs")
            frame = FeedFrame(candle, mode, received, raw)
        expected = frame.candle
        if (
            canonical_json(expected.as_dict()) != row["candle"]
            or expected.opened_at.isoformat() != row["opened_at"]
        ):
            raise ValueError("stored monitor candle differs from its preserved frame")
        if expected.contract != MgcContract(definition["contract"]):
            raise ValueError("monitor contract differs from its manifest")
        return expected
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MonitorInputIntegrityError(
            f"monitor frame audit failed at {row['opened_at']}: {exc}"
        ) from None


def verify_monitor(store: DatasetStore, monitor_id: str) -> dict[str, object]:
    """Audit a frozen input bound without changing monitor or strategy state.

    Existing applied rows and source bytes are immutable through the application.
    New source frames above the captured bound do not affect this audit. A gap
    fill inside that bound changes its count and requires a retry.
    """
    with store.connection() as db:
        db.execute("BEGIN")
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        row = db.execute("SELECT * FROM monitor_runs WHERE id = ?", (monitor_id,)).fetchone()
        if row is None:
            raise ValueError("unknown monitor")
        if sha256(row["definition"].encode()).hexdigest() != monitor_id:
            raise ValueError("monitor manifest does not match its immutable identity")
        definition: dict[str, Any] = json.loads(row["definition"])
        calendar = SessionCalendar.from_dict(definition["calendar"])
        if calendar.sessions is None:
            raise ValueError("monitor audit requires its original dated calendar")
        state = dict(row)
        bounds = db.execute(
            "SELECT count(*) AS count, max(opened_at) AS through_open "
            "FROM monitor_minutes WHERE run_id = ?",
            (monitor_id,),
        ).fetchone()
        total, through = bounds["count"], bounds["through_open"]
    expected = calendar.windows[0].start if calendar.windows else None
    applied = unknown_clocks = eligible = count = 0
    last_applied = None
    last_observed: datetime | None = None
    after = ""
    while through is not None:
        fields = (
            "applied_at, execution_eligible"
            if schema >= 7
            else "applied_at, 0 AS execution_eligible"
            if schema >= 6
            else "NULL AS applied_at, 0 AS execution_eligible"
        )
        with store.connection() as db:
            page = db.execute(
                f"""SELECT opened_at, candle, received_at, source_status, applied, {fields},
                    CASE WHEN length(raw) <= {MAX_FRAME_BYTES} THEN raw ELSE NULL END AS raw
                    FROM monitor_minutes WHERE run_id = ? AND opened_at > ? AND opened_at <= ?
                    ORDER BY opened_at LIMIT 1000""",
                (monitor_id, after, through),
            ).fetchall()
        if not page:
            break
        for item in page:
            candle = preserved_monitor_candle(dict(item), definition)
            count += 1
            if not calendar.permits(candle.opened_at):
                raise ValueError("monitor input lies outside its reviewed calendar")
            if state["last_applied"] is not None and item["opened_at"] <= state["last_applied"]:
                if not item["applied"] or candle.opened_at != expected:
                    raise ValueError("monitor applied prefix has a missing or unordered minute")
                applied += 1
                last_applied = item["opened_at"]
                observed = None
                if item["applied_at"] is None:
                    unknown_clocks += 1
                else:
                    observed = datetime.fromisoformat(item["applied_at"])
                    if observed.tzinfo is not UTC or item["applied_at"] < item["received_at"]:
                        raise ValueError("monitor application precedes receipt or is not UTC")
                    if last_observed is not None and observed < last_observed:
                        raise ValueError("known monitor observations regress in source order")
                    last_observed = observed
                if item["execution_eligible"]:
                    if (
                        observed is None
                        or item["source_status"] not in ("real_time", "simulated")
                        or (observed - candle.available_at).total_seconds()
                        > definition["monitor_config"]["stale_seconds"]
                    ):
                        raise ValueError(
                            "monitor execution eligibility lacks fresh observation evidence"
                        )
                    eligible += 1
                index = (
                    bisect_right(
                        calendar.windows, candle.opened_at, key=lambda window: window.start
                    )
                    - 1
                )
                following = candle.opened_at + MINUTE
                expected = (
                    following
                    if following < calendar.windows[index].end
                    else calendar.windows[index + 1].start
                    if index + 1 < len(calendar.windows)
                    else None
                )
        after = page[-1]["opened_at"]
        if len(page) < 1000:
            break
    if count != total:
        raise ValueError("monitor input bound changed during verification; retry the audit")
    if (
        last_applied != state["last_applied"]
        or (expected.isoformat() if expected else None) != state["next_open"]
    ):
        raise ValueError("monitor cursor differs from its reconstructed applied prefix")
    return {
        "monitor_audit_version": MONITOR_AUDIT_VERSION,
        "monitor_id": monitor_id,
        "contract": definition["contract"],
        "source_status": definition["monitor_config"]["source_status"],
        "through_open": through,
        "applied_through": state["last_applied"],
        "minutes_verified": count,
        "applied_minutes_verified": applied,
        "legacy_unknown_observation_rows": unknown_clocks,
        "execution_eligible_rows": eligible,
        "verified": True,
        "scope": "preserved_input_equivalence_and_applied_continuity",
        "limitations": [
            "Source classification and entitlements are adapter/operator assertions.",
            "This audit does not reproduce the detector-event or alert journals.",
            "Unknown legacy observation clocks are not reconstructed.",
        ],
    }
