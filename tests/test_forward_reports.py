import json
import subprocess
from datetime import date
from pathlib import Path
from sys import executable

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
from aurum.monitoring import MarketMonitor, MonitorConfig
from aurum.providers import FeedStatus
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


def test_completed_synthetic_periods_publish_once_and_preserve_earlier_report(
    tmp_path: Path,
) -> None:
    friday = minute_timestamp("2025-03-07T13:00Z")
    monday = minute_timestamp("2025-03-10T13:00Z")
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

    send("2025-03-07T13:00Z")
    ForwardPaper(store, run).sync()
    assert publish_completed_periods(store, run) == []
    send("2025-03-07T13:01Z")
    cli = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    output = subprocess.run(
        [*cli, "forward-sync", run, "--auto-reports"], check=True, capture_output=True, text=True
    )
    references = json.loads(output.stdout)["published_report_ids"]
    assert len(references) == 2
    assert publish_completed_periods(store, run) == []
    daily = save_forward_report(store, run, ForwardPeriod.DAILY, friday.date())
    weekly = save_forward_report(store, run, ForwardPeriod.WEEKLY, friday.date())
    assert set(references) == {daily, weekly}

    send("2025-03-10T13:00Z")
    send("2025-03-10T13:01Z")
    ForwardPaper(store, run).sync()
    (published,) = publish_completed_periods(store, run)
    assert published == save_forward_report(store, run, ForwardPeriod.DAILY, monday.date())
    assert save_forward_report(store, run, ForwardPeriod.DAILY, friday.date()) == daily
    assert save_forward_report(store, run, ForwardPeriod.WEEKLY, friday.date()) == weekly
    assert publish_completed_periods(store, run) == []
