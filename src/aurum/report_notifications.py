"""Queue source-labeled summaries of immutable forward paper reports."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from aurum.market_data import canonical_json
from aurum.storage import DatasetStore

REPORT_NOTIFICATION_VERSION = 1


def queue_forward_report(
    store: DatasetStore, report_id: str, *, now: datetime | None = None
) -> str:
    with store.connection(write=True) as db:
        return enqueue_forward_report(db, report_id, now=now)


def enqueue_forward_report(
    db: sqlite3.Connection, report_id: str, *, now: datetime | None = None
) -> str:
    """Join an existing publication transaction, or use queue_forward_report."""
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("report notification clock must be aware UTC")
    row = db.execute("SELECT * FROM analysis_reports WHERE id = ?", (report_id,)).fetchone()
    if row is None or row["kind"] != "forward_paper_research":
        raise ValueError("notification requires a saved forward paper research report")
    if now < datetime.fromisoformat(row["created_at"]):
        raise ValueError("report notification cannot precede report creation")
    definition: dict[str, Any] = json.loads(row["definition"])
    result: dict[str, Any] = json.loads(row["result"])
    monitor_id = definition["monitor_id"]
    alert_id = f"{monitor_id}/report/{report_id}"
    payload = {
        "alert_id": alert_id,
        "event": "forward_paper_report",
        "severity": "research",
        "report_notification_version": REPORT_NOTIFICATION_VERSION,
        "report_id": report_id,
        "forward_id": definition["forward_id"],
        "period": definition["period"],
        "first_trade_date": definition["first_trade_date"],
        "end_trade_date_exclusive": definition["end_trade_date_exclusive"],
        "contract": result["contract"],
        "source": result["source"],
        "data_kind": result["data_kind"],
        "execution_mode": "paper_research",
        "summary": result["period"],
        "strategy_version": result["strategy_version"],
        "strategy_config": result["strategy_config"],
        "execution_config": result["execution_config"],
        "report_created_at": row["created_at"],
        "queued_at": now.isoformat(),
    }
    db.execute(
        "INSERT INTO monitor_alerts VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (monitor_id, alert_id, canonical_json(payload), now.isoformat()),
    )
    return alert_id
