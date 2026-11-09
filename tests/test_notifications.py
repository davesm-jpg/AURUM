import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from urllib.request import Request

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.feed_frames import FeedFrame
from aurum.instruments import MgcContract
from aurum.market_data import Candle
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import MarketMonitor, MonitorConfig
from aurum.notification_transport import HttpAlertChannel
from aurum.notifications import (
    Channel,
    DeliveryFailure,
    DeliveryPreferences,
    DeliveryWorker,
    cancel_delivery,
    delivery_status,
)
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-03-10T13:00Z")
AT = START + MINUTE


@dataclass
class SyntheticChannel:
    channel: Channel = Channel.TELEGRAM
    destination_key: str = "synthetic-destination"
    failures: list[Exception] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)

    def send(self, alert: dict[str, object]) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(str(alert["alert_id"]))


def fixture_monitor(tmp_path: Path) -> MarketMonitor:
    store = DatasetStore(tmp_path / "notifications.sqlite")
    span = TradingWindow(START, START + 10 * MINUTE)
    calendar = SessionCalendar(
        "synthetic notification fixture",
        "fixture-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    contract = MgcContract("MGC-2025-04")
    monitor = MarketMonitor(
        store,
        calendar,
        contract,
        MonitorConfig("synthetic alert source", FeedStatus.REAL_TIME),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
    )
    monitor.set_connection(True, START)
    first = Candle(contract, START, 29000, 29000, 29000, 29000, 1)
    monitor.ingest(FeedFrame.from_replay(first, FeedStatus.REAL_TIME, AT))
    return monitor


def add_alert(monitor: MarketMonitor, alert_id: str, *, score: int | None = None) -> None:
    payload: dict[str, object] = {
        "alert_id": alert_id,
        "event": "data_quality_warning",
        "severity": "warning",
        "contract": monitor.contract.code,
        "state": "stale",
    }
    if score is not None:
        payload.update(
            event="paper_research_alert",
            severity="research",
            setup={"score": {"total": score}, "context": {"session": "new_york"}},
        )
    with monitor.store.connection(write=True) as db:
        db.execute(
            "INSERT INTO monitor_alerts VALUES (?, ?, ?, ?)",
            (monitor.monitor_id, alert_id, json.dumps(payload), AT.isoformat()),
        )


def test_retry_attempts_and_concurrent_duplicate_suppression(tmp_path: Path) -> None:
    monitor = fixture_monitor(tmp_path)
    add_alert(monitor, "synthetic-alert")
    transport = SyntheticChannel(failures=[DeliveryFailure("http_429", retry_seconds=5)])
    worker = DeliveryWorker(monitor.store, monitor.monitor_id, transport)
    assert worker.run_once(clock=lambda: AT) == 1
    assert worker.run_once(clock=lambda: AT + timedelta(seconds=14)) == 0
    later = AT + timedelta(seconds=16)
    with ThreadPoolExecutor(2) as pool:
        counts = list(pool.map(lambda _: worker.run_once(clock=lambda: later), range(2)))
    assert sum(counts) == 1
    assert transport.sent == ["synthetic-alert"]
    (state,) = list(delivery_status(monitor.store, monitor.monitor_id))
    assert state["state"] == "sent"
    attempts = state["attempt_log"]
    assert isinstance(attempts, list)
    assert [item["outcome"] for item in attempts] == ["retry", "sent"]


def test_quiet_hours_filters_rate_limit_and_cancel(tmp_path: Path) -> None:
    monitor = fixture_monitor(tmp_path)
    add_alert(monitor, "synthetic-low-score", score=30)
    add_alert(monitor, "synthetic-canceled")
    add_alert(monitor, "synthetic-good")
    add_alert(monitor, "synthetic-other")
    transport = SyntheticChannel()
    preferences = DeliveryPreferences(
        timezone="UTC",
        quiet_start_minute=13 * 60,
        quiet_end_minute=13 * 60 + 2,
        min_score=65,
        attempts_per_hour=1,
    )
    worker = DeliveryWorker(monitor.store, monitor.monitor_id, transport, preferences)
    assert worker.run_once(clock=lambda: AT) == 0
    cancel_delivery(monitor.store, monitor.monitor_id, "synthetic-canceled", AT)
    assert worker.run_once(clock=lambda: AT + MINUTE) == 1
    states = {
        item["alert_id"]: item["state"]
        for item in delivery_status(monitor.store, monitor.monitor_id)
    }
    assert states["synthetic-low-score"] == "filtered"
    assert states["synthetic-canceled"] == "canceled"
    assert {states["synthetic-good"], states["synthetic-other"]} == {"sent", "pending"}


def test_health_gate_expires_old_signals_and_uncertain_attempts_do_not_resend(
    tmp_path: Path,
) -> None:
    monitor = fixture_monitor(tmp_path)
    add_alert(monitor, "synthetic-paper", score=90)
    add_alert(monitor, "synthetic-health")
    transport = SyntheticChannel(failures=[DeliveryFailure("unknown_result", uncertain=True)])
    worker = DeliveryWorker(monitor.store, monitor.monitor_id, transport)
    stale = AT + 2 * MINUTE
    assert worker.run_once(clock=lambda: stale) == 1
    assert worker.run_once(clock=lambda: stale) == 0
    states = {
        item["alert_id"]: item["state"]
        for item in delivery_status(monitor.store, monitor.monitor_id)
    }
    assert states == {"synthetic-paper": "pending", "synthetic-health": "uncertain"}
    worker.run_once(clock=lambda: AT + 16 * MINUTE)
    assert transport.sent == []
    states = {
        item["alert_id"]: item["state"]
        for item in delivery_status(monitor.store, monitor.monitor_id)
    }
    assert states["synthetic-paper"] == "expired"


@pytest.mark.parametrize("channel", [Channel.TELEGRAM, Channel.DISCORD])
def test_https_payloads_are_confirmed_without_real_messages(
    channel: Channel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SyntheticOpener:
        def open(self, request: Request, *, timeout: int) -> BytesIO:
            assert request.get_method() == "POST" and timeout == 20
            assert isinstance(request.data, bytes)
            body = json.loads(request.data)
            if channel is Channel.TELEGRAM:
                assert request.host == "api.telegram.org"
                assert body["chat_id"] == "-123456"
                assert "paper research" in body["text"]
                return BytesIO(b'{"ok": true}')
            assert request.host == "discord.com"
            assert request.full_url.endswith("?wait=true")
            assert body["allowed_mentions"] == {"parse": []}
            return BytesIO(b'{"id": "synthetic-message"}')

    monkeypatch.setattr("aurum.notification_transport.build_opener", lambda _: SyntheticOpener())
    transport = (
        HttpAlertChannel.telegram("123:synthetic-token", "-123456")
        if channel is Channel.TELEGRAM
        else HttpAlertChannel.discord("https://discord.com/api/webhooks/123/synthetic-token")
    )
    transport.send(
        {
            "event": "data_quality_warning",
            "contract": "MGC-2025-04",
            "state": "stale",
            "alert_id": "synthetic-alert",
        }
    )
    assert "synthetic-token" not in repr(transport)
