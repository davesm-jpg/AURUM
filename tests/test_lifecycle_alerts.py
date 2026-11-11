"""Synthetic market inputs and fake transports only; no external messages."""

import json
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from test_market_structure import (
    BEARISH,
    CONTRACT,
    START,
    TIMEFRAME,
    prepare_synthetic_replay,
    synthetic_bar,
)
from test_notifications import SyntheticChannel

from aurum.exchange_calendar import SessionCalendar
from aurum.feed_frames import FeedFrame
from aurum.market_data import Candle
from aurum.market_structure import PivotRule, StructureConfig
from aurum.monitoring import MarketMonitor, MonitorConfig, monitor_alerts
from aurum.notification_transport import render_alert
from aurum.notifications import DeliveryPreferences, DeliveryWorker, Severity, delivery_status
from aurum.providers import FeedStatus
from aurum.strategy import DEFAULT_STRATEGY_CONFIG


def fixture(tmp_path: Path, enabled: bool) -> tuple[MarketMonitor, list[Candle]]:
    bars = [
        *(synthetic_bar(index, *bounds) for index, bounds in enumerate(BEARISH)),
        synthetic_bar(9, 81, 94, 85),
        synthetic_bar(10, 78, 90, 89),
        synthetic_bar(11, 88, 118, 116),
        synthetic_bar(12, 91, 118, 110),
        synthetic_bar(13, 103, 120, 111),
        synthetic_bar(14, 100, 122, 110),
    ]
    store, dataset = prepare_synthetic_replay(tmp_path, bars=bars)
    schedule = SessionCalendar.from_dict(store.describe(dataset)["provenance"]["calendar"])
    config = MonitorConfig(
        "synthetic lifecycle fixture",
        FeedStatus.REAL_TIME,
        timeframe=TIMEFRAME,
        lifecycle_alerts=enabled,
    )
    monitor = MarketMonitor(
        store, schedule, CONTRACT, config, StructureConfig(PivotRule(1, 1)), DEFAULT_STRATEGY_CONFIG
    )
    monitor.set_connection(True, START)
    # Release the source reader before writing monitor minutes to the same store.
    return monitor, list(store.replay(dataset))


def apply(monitor: MarketMonitor, candles: list[Candle], *, historical: bool = False) -> None:
    mode = FeedStatus.HISTORICAL if historical else FeedStatus.REAL_TIME
    delay = timedelta(minutes=3) if historical else timedelta(0)
    for candle in candles:
        monitor.ingest(FeedFrame.from_replay(candle, mode, candle.available_at + delay))


@pytest.mark.parametrize("enabled", (False, True))
def test_setup_notices_opt_in_collapse_and_restart_without_duplicate_delivery(
    tmp_path: Path,
    enabled: bool,
) -> None:
    monitor, candles = fixture(tmp_path, enabled)
    apply(monitor, candles[:35])
    restarted = MarketMonitor(
        monitor.store,
        monitor.calendar,
        CONTRACT,
        monitor.config,
        StructureConfig(PivotRule(1, 1)),
        DEFAULT_STRATEGY_CONFIG,
    )
    apply(restarted, candles[35:])
    alerts: list[dict[str, Any]] = [
        item
        for item in monitor_alerts(monitor.store, monitor.monitor_id)
        if str(item["event"]).startswith("paper_")
    ]
    counts = Counter(item["event"] for item in alerts)
    assert counts["paper_research_alert"] == 1
    assert counts["paper_setup_developing"] == (4 if enabled else 0)
    assert counts["paper_setup_withdrawn"] == (1 if enabled else 0)
    assert ("lifecycle_alerts" in monitor.config.as_dict()) is enabled
    with monitor.store.connection() as sql:
        definition = json.loads(
            sql.execute(
                "SELECT definition FROM monitor_runs WHERE id = ?", (monitor.monitor_id,)
            ).fetchone()[0]
        )
    assert ("lifecycle_alerts" in definition["monitor_config"]) is enabled
    assert replace(monitor.config, lifecycle_alerts=False).as_dict() == {
        key: value for key, value in monitor.config.as_dict().items() if key != "lifecycle_alerts"
    }
    developing = [item for item in alerts if item["event"] == "paper_setup_developing"]
    keys = [(item["setup"]["setup_id"], item["setup"]["confirmed_at"]) for item in developing]
    assert len(keys) == len(set(keys))
    if enabled:
        at_mss_close = [
            item
            for item in developing
            if item["setup"]["confirmed_at"] == "2025-03-10T14:00:00+00:00"
        ]
        assert len(at_mss_close) == 1 and at_mss_close[0]["setup"]["state"] == "retest_pending"
        rendered = render_alert(developing[0])
        assert "DEVELOPING SETUP" in rendered and "unconfirmed" in rendered
        assert "Observed UTC" in rendered and "Source class real_time" in rendered
        assert not DeliveryPreferences(min_score=1).allows(developing[0])
        assert DeliveryPreferences().allows(developing[0])
    apply(restarted, candles[-1:])
    assert list(monitor_alerts(monitor.store, monitor.monitor_id)) == list(
        monitor_alerts(restarted.store, restarted.monitor_id)
    )
    assert len(
        [
            item
            for item in monitor_alerts(monitor.store, monitor.monitor_id)
            if str(item["event"]).startswith("paper_")
        ]
    ) == len(alerts)


def test_recovered_invalidation_cancels_queued_developing_notices_and_remains_informational(
    tmp_path: Path,
) -> None:
    monitor, candles = fixture(tmp_path, True)
    apply(monitor, candles[:35])
    channel = SyntheticChannel()
    quiet = DeliveryPreferences(quiet_start_minute=0, quiet_end_minute=1439)
    worker = DeliveryWorker(monitor.store, monitor.monitor_id, channel, quiet)
    assert worker.run_once(clock=lambda: candles[34].available_at) == 0
    assert any(
        item["state"] == "pending" for item in delivery_status(monitor.store, monitor.monitor_id)
    )
    apply(monitor, candles[35:50], historical=True)
    alerts = list(monitor_alerts(monitor.store, monitor.monitor_id))
    withdrawn = next(item for item in alerts if item["event"] == "paper_setup_withdrawn")
    assert withdrawn["data_status"] == "historical"
    assert withdrawn["feed_age_seconds"] == 180
    text = render_alert(withdrawn)
    assert "SETUP WITHDRAWN" in text and "sweep invalidation" in text
    assert "Source class historical" in text
    assert any(
        item["state"] == "canceled" for item in delivery_status(monitor.store, monitor.monitor_id)
    )
    now = candles[49].available_at + timedelta(minutes=3)
    monitor.set_connection(False, now)
    informational = DeliveryWorker(
        monitor.store,
        monitor.monitor_id,
        channel,
        DeliveryPreferences(severities=(Severity.WARNING,)),
    )
    informational.run_once(clock=lambda: now)
    assert withdrawn["alert_id"] in channel.sent
    assert not any("/developing/" in reference for reference in channel.sent)
    before = list(channel.sent)
    informational.run_once(clock=lambda: now)
    assert channel.sent == before


def test_historical_inputs_create_no_developing_or_confirmed_notices(tmp_path: Path) -> None:
    monitor, candles = fixture(tmp_path, True)
    apply(monitor, candles, historical=True)
    assert not [
        item
        for item in monitor_alerts(monitor.store, monitor.monitor_id)
        if str(item["event"]).startswith("paper_")
    ]
    with pytest.raises(ValueError, match="real-time"):
        replace(monitor.config, source_status=FeedStatus.SIMULATED)


def test_developing_delivery_waits_for_fresh_connected_monitor(tmp_path: Path) -> None:
    monitor, candles = fixture(tmp_path, True)
    apply(monitor, candles[:35])
    now = candles[34].available_at
    channel = SyntheticChannel()
    preferences = DeliveryPreferences(severities=(Severity.RESEARCH,))
    worker = DeliveryWorker(monitor.store, monitor.monitor_id, channel, preferences)
    monitor.set_connection(False, now)
    assert worker.run_once(clock=lambda: now) == 0
    monitor.set_connection(True, now + timedelta(seconds=1))
    assert worker.run_once(clock=lambda: now + timedelta(seconds=1)) == 1
    assert len(channel.sent) == 1 and "/developing/" in channel.sent[0]
    assert worker.run_once(clock=lambda: now + timedelta(seconds=1)) == 0
