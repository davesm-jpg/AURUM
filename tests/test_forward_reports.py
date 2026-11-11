import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from sys import executable

import pytest
from test_notifications import SyntheticChannel

import aurum.forward_reports as report_module
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.feed_frames import FeedFrame
from aurum.forward import ForwardPaper, register_forward
from aurum.forward_reports import (
    ForwardPeriod,
    period_bounds,
    publish_completed_periods,
    save_forward_report,
)
from aurum.instruments import MgcContract
from aurum.market_data import Candle
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import MarketMonitor, MonitorConfig, monitor_alerts
from aurum.notification_transport import render_alert
from aurum.notifications import DeliveryPreferences, DeliveryWorker, Severity, delivery_status
from aurum.providers import FeedStatus
from aurum.report_notifications import enqueue_forward_report, queue_forward_report
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
from aurum.timestamps import MINUTE, minute_timestamp


def test_synthetic_forward_period_boundaries_follow_exchange_trade_dates() -> None:
    assert period_bounds(ForwardPeriod.DAILY, date(2024, 2, 29)) == (
        date(2024, 2, 29),
        date(2024, 3, 1),
    )
    assert period_bounds(ForwardPeriod.WEEKLY, date(2025, 1, 1)) == (
        date(2024, 12, 30),
        date(2025, 1, 6),
    )
    assert period_bounds(ForwardPeriod.MONTHLY, date(2025, 12, 31)) == (
        date(2025, 12, 1),
        date(2026, 1, 1),
    )


@pytest.mark.parametrize("notify", (False, True))
def test_completed_synthetic_periods_publish_once_and_preserve_earlier_report(
    tmp_path: Path,
    notify: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    friday = minute_timestamp("2025-01-31T13:00Z")
    monday = minute_timestamp("2025-02-03T13:00Z")
    fri = TradingWindow(friday, friday + 2 * MINUTE)
    mon = TradingWindow(monday, monday + 2 * MINUTE)
    calendar = SessionCalendar(
        "synthetic period publication calendar",
        "synthetic-v1",
        TradingWindow(fri.start, mon.end),
        sessions=(TradingSession(friday.date(), (fri,)), TradingSession(monday.date(), (mon,))),
    )
    contract = MgcContract("MGC-2025-04")
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    monitor = MarketMonitor(
        store,
        calendar,
        contract,
        MonitorConfig("synthetic report publication", FeedStatus.SIMULATED),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
    )
    run = register_forward(store, monitor.monitor_id, friday, synthetic=True)
    monitor.set_connection(True, friday)

    def send(opened: str) -> None:
        candle = Candle(contract, minute_timestamp(opened), 29000, 29000, 29000, 29000, 1)
        monitor.ingest(FeedFrame.from_replay(candle, FeedStatus.SIMULATED, candle.available_at))

    send("2025-01-31T13:00Z")
    ForwardPaper(store, run).sync()
    assert publish_completed_periods(store, run) == []
    send("2025-01-31T13:01Z")
    if notify:
        ForwardPaper(store, run).sync()

        def fail_after_queue(
            db: sqlite3.Connection, report_id: str, *, now: datetime | None = None
        ) -> None:
            enqueue_forward_report(db, report_id, now=now)
            raise RuntimeError("synthetic publication failure")

        with monkeypatch.context() as patch:
            patch.setattr(report_module, "enqueue_forward_report", fail_after_queue)
            with pytest.raises(RuntimeError, match="synthetic publication failure"):
                publish_completed_periods(store, run, notify=True)
        with store.connection() as db:
            assert db.execute("SELECT count(*) FROM forward_report_publications").fetchone()[0] == 0
        assert list(monitor_alerts(store, monitor.monitor_id)) == []
    cli = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    output = subprocess.run(
        [*cli, "forward-sync", run, "--auto-reports", *(["--notify-reports"] if notify else [])],
        check=True,
        capture_output=True,
        text=True,
    )
    references = json.loads(output.stdout)["published_report_ids"]
    assert len(references) == 3
    assert publish_completed_periods(store, run) == []
    daily = save_forward_report(store, run, ForwardPeriod.DAILY, friday.date())
    weekly = save_forward_report(store, run, ForwardPeriod.WEEKLY, friday.date())
    monthly = save_forward_report(store, run, ForwardPeriod.MONTHLY, friday.date())
    assert set(references) == {daily, weekly, monthly}
    assert len(list(monitor_alerts(store, monitor.monitor_id))) == (3 if notify else 0)

    send("2025-02-03T13:00Z")
    send("2025-02-03T13:01Z")
    ForwardPaper(store, run).sync()
    (published,) = publish_completed_periods(store, run, notify=notify)
    assert published == save_forward_report(store, run, ForwardPeriod.DAILY, monday.date())
    assert save_forward_report(store, run, ForwardPeriod.DAILY, friday.date()) == daily
    assert save_forward_report(store, run, ForwardPeriod.WEEKLY, friday.date()) == weekly
    assert publish_completed_periods(store, run) == []

    # Manual publication can queue a report saved without notifications. Concurrent
    # attempts retain the first queued clock and payload and never resend it.
    with ThreadPoolExecutor(max_workers=2) as pool:
        alert_ids = list(pool.map(lambda _: queue_forward_report(store, daily), range(2)))
    assert len(set(alert_ids)) == 1
    alerts = list(monitor_alerts(store, monitor.monitor_id))
    assert len(alerts) == (4 if notify else 1)
    alert = next(item for item in alerts if item["report_id"] == daily)
    original = dict(alert)
    response = subprocess.run(
        [*cli, "notify-report", daily], check=True, capture_output=True, text=True
    )
    assert json.loads(response.stdout)["alert_id"] == alert_ids[0]
    assert (
        next(
            item for item in monitor_alerts(store, monitor.monitor_id) if item["report_id"] == daily
        )
        == original
    )
    assert alert["data_kind"] == "synthetic_forward_verification"
    text = render_alert(alert)
    assert "synthetic_forward_verification" in text and "Completed trades 0" in text
    assert "unavailable with this sample" in text and "2/2 reviewed minutes" in text
    assert "Report " + daily in text
    # Informational reports remain deliverable when the feed is stale. The common
    # delivery ledger still handles duplicate suppression and severity preferences.
    channel = SyntheticChannel()
    worker = DeliveryWorker(store, monitor.monitor_id, channel)
    assert worker.run_once() == len(alerts)
    assert set(channel.sent) == {item["alert_id"] for item in alerts}
    assert DeliveryWorker(store, monitor.monitor_id, channel).run_once() == 0
    assert all(item["state"] == "sent" for item in delivery_status(store, monitor.monitor_id))
    filtered = SyntheticChannel(destination_key="synthetic-filtered")
    preferences = DeliveryPreferences(severities=(Severity.WARNING,))
    assert DeliveryWorker(store, monitor.monitor_id, filtered, preferences).run_once() == 0
    with pytest.raises(ValueError, match="saved forward"):
        queue_forward_report(store, "unknown")
    with pytest.raises(ValueError, match="precede report creation"):
        queue_forward_report(store, daily, now=friday)

    invalid = subprocess.run(
        [*cli, "forward-sync", run, "--notify-reports"], capture_output=True, text=True
    )
    assert invalid.returncode and "require --auto-reports" in invalid.stderr
