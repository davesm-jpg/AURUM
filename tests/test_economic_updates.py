import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from sys import executable

import pytest
from test_monitoring import CONTRACT, START, calendar

from aurum.economic_events import Announcement, EconomicCalendar, EconomicSourceKind, EventRisk
from aurum.economic_updates import adopt_economic_snapshot, economic_timeline, economic_updates
from aurum.feed_frames import FeedFrame
from aurum.monitoring import MarketMonitor, MonitorConfig, market_events_page, monitor_events
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
from aurum.timestamps import MINUTE


def snapshot(*, elevated: bool = False) -> EconomicCalendar:
    return EconomicCalendar(
        "Synthetic reviewed update",
        "fixture-v1",
        calendar((-30, 100)).coverage,
        START - MINUTE,
        (Announcement("synthetic-release", "Synthetic release", START + 10 * MINUTE, START),)
        if elevated
        else (),
        data_kind=EconomicSourceKind.SYNTHETIC,
    )


def monitor(store: DatasetStore) -> MarketMonitor:
    from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG

    return MarketMonitor(
        store,
        calendar((0, 30)),
        CONTRACT,
        MonitorConfig("synthetic reviewed update monitor", FeedStatus.SIMULATED),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
        economic=snapshot(),
    )


def test_economic_updates_reject_retroactive_and_future_knowledge(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    instance = monitor(store)
    instance.set_connection(True, START)
    from test_monitoring import frame

    instance.ingest(frame(0, received=3, mode=FeedStatus.SIMULATED))
    for at in (START + MINUTE, START + 3 * MINUTE):
        with pytest.raises(ValueError, match="follow all processed"):
            adopt_economic_snapshot(
                store, instance.monitor_id, snapshot(), activated_at=at, reason="review"
            )
    activated = START + 4 * MINUTE
    with pytest.raises(ValueError, match="precedes supplied"):
        adopt_economic_snapshot(
            store,
            instance.monitor_id,
            replace(snapshot(), coverage_known_at=activated + MINUTE),
            activated_at=activated,
            reason="review",
        )
    with pytest.raises(ValueError, match="explicit source"):
        adopt_economic_snapshot(
            store,
            instance.monitor_id,
            replace(snapshot(), data_kind=None),
            activated_at=activated,
            reason="review",
        )

    def adopt() -> str:
        return adopt_economic_snapshot(
            store,
            instance.monitor_id,
            snapshot(elevated=True),
            activated_at=activated,
            reason="synthetic reviewed publication",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = tuple(executor.map(lambda _: adopt(), range(2)))
    assert ids[0] == ids[1]
    assert len(economic_updates(store, instance.monitor_id)) == 1
    with store.connection() as db:
        timeline = economic_timeline(db, instance.monitor_id, snapshot())
        assert timeline.at(db, activated - timedelta(microseconds=1)) == (snapshot(), None)
        selected, selected_id = timeline.at(db, activated)
        assert selected is not None and selected_id == ids[0]
        assert selected.assess(activated, 15).risk is EventRisk.ELEVATED
    unavailable = adopt_economic_snapshot(
        store,
        instance.monitor_id,
        None,
        activated_at=activated + MINUTE,
        reason="synthetic publication unavailable",
    )
    with store.connection() as db:
        assert economic_timeline(db, instance.monitor_id, snapshot()).at(
            db, activated + MINUTE
        ) == (None, unavailable)


def test_source_context_and_restart_follow_the_activation_timeline(tmp_path: Path) -> None:
    from test_monitoring import frame

    store = DatasetStore(tmp_path / "synthetic.sqlite")
    instance = monitor(store)
    instance.set_connection(True, START)
    for index in range(5):
        instance.ingest(frame(index, mode=FeedStatus.SIMULATED))
    prefix = market_events_page(store, instance.monitor_id, limit=500)
    update_id = adopt_economic_snapshot(
        store,
        instance.monitor_id,
        snapshot(elevated=True),
        activated_at=START + 6 * MINUTE,
        reason="synthetic schedule review",
    )
    restarted = monitor(store)
    assert restarted.monitor_id == instance.monitor_id
    for index in range(5, 11):
        restarted.ingest(frame(index, mode=FeedStatus.SIMULATED))
    evidence = market_events_page(store, instance.monitor_id, limit=500)
    assert evidence[: len(prefix)] == prefix
    contexts = [event for event in evidence if event["event"] == "market_context_observed"]
    for event, risk in zip(contexts[:2], ("clear", "elevated"), strict=True):
        context = event["context"]
        assert isinstance(context, dict)
        assert context["economic"]["risk"] == risk
    assert contexts[1]["source_economic_update_id"] == update_id
    unavailable = adopt_economic_snapshot(
        store,
        instance.monitor_id,
        None,
        activated_at=START + 12 * MINUTE,
        reason="synthetic source outage",
    )
    continued = monitor(store)
    for index in range(11, 15):
        continued.ingest(frame(index, mode=FeedStatus.SIMULATED))
    last = market_events_page(store, instance.monitor_id, kind="market_context_observed")[-1]
    context = last["context"]
    assert isinstance(context, dict)
    assert context["economic"] == {"risk": "unknown", "event_ids": []}
    assert last["source_economic_update_id"] == unavailable
    assert monitor(store).monitor_id == instance.monitor_id


def test_observation_update_gates_forward_without_rewriting_source_setup(tmp_path: Path) -> None:
    from test_market_structure import BEARISH, TIMEFRAME, prepare_synthetic_replay, synthetic_bar

    from aurum.exchange_calendar import SessionCalendar
    from aurum.forward import ForwardPaper, forward_events, register_forward
    from aurum.market_structure import PivotRule, StructureConfig

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
    config = MonitorConfig("synthetic observed update", FeedStatus.SIMULATED, timeframe=TIMEFRAME)
    structure = StructureConfig(PivotRule(1, 1))
    strategy = replace(DEFAULT_STRATEGY_CONFIG, require_clear_event_risk=True)
    baseline = snapshot()
    instance = MarketMonitor(
        store, schedule, CONTRACT, config, structure, strategy, economic=baseline
    )
    forward = register_forward(store, instance.monitor_id, START, synthetic=True)
    instance.set_connection(True, START)
    decision = bars[12].available_at
    update_id = None
    for candle in list(store.replay(dataset)):
        receipt = candle.available_at
        if receipt == decision:
            update_id = adopt_economic_snapshot(
                store,
                instance.monitor_id,
                None,
                activated_at=decision + timedelta(seconds=2),
                reason="synthetic observed outage",
            )
            receipt += timedelta(seconds=5)
        instance.ingest(FeedFrame.from_replay(candle, FeedStatus.SIMULATED, receipt))
    qualified = [
        event
        for event in monitor_events(store, instance.monitor_id)
        if event.get("state") == "paper_signal"
    ]
    assert len(qualified) == 1
    signal = qualified[0]
    context = signal["context"]
    assert isinstance(context, dict)
    assert context["economic"] == {"risk": "clear", "event_ids": []}
    assert "source_economic_update_id" not in signal
    assert signal["observed_economic_update_id"] == update_id
    assert signal["observed_economic"] == {"risk": "unknown", "event_ids": []}
    assert signal["observation_event_filter_passed"] is False
    ForwardPaper(store, forward).sync()
    events = forward_events(store, forward)
    assert not any(event["event"] == "paper_entry_filled" for event in events)
    before = json.dumps(qualified, sort_keys=True)
    MarketMonitor(store, schedule, CONTRACT, config, structure, strategy, economic=baseline)
    assert (
        json.dumps(
            [
                event
                for event in monitor_events(store, instance.monitor_id)
                if event.get("state") == "paper_signal"
            ],
            sort_keys=True,
        )
        == before
    )


def test_update_api_and_cli_keep_server_clock_and_reject_synthetic_live_context(
    tmp_path: Path,
) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    instance = monitor(store)
    route = f"/api/monitors/{instance.monitor_id}/economic-updates"
    with http.TestClient(api.create_app(store)) as client:
        request = {"economic_calendar": snapshot().as_dict(), "reason": "synthetic review"}
        assert (
            client.post(route, json={**request, "activated_at": START.isoformat()}).status_code
            == 422
        )
        response = client.post(route, json=request)
        assert response.status_code == 201
        update = client.get(route).json()[0]
        assert update["update_id"] == response.json()["update_id"]
        assert update["activated_at"] > START.isoformat()
    command = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    output = subprocess.run(
        [*command, "monitor-economic-updates", instance.monitor_id],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(output.stdout) == economic_updates(store, instance.monitor_id)
    from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG

    live = MarketMonitor(
        store,
        calendar((0, 30)),
        CONTRACT,
        MonitorConfig("synthetic fixture testing live classification", FeedStatus.REAL_TIME),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
    )
    with pytest.raises(ValueError, match="simulated monitor"):
        adopt_economic_snapshot(
            store,
            live.monitor_id,
            snapshot(),
            activated_at=START,
            reason="synthetic classification test",
        )


def test_corrupted_activation_stops_replay(tmp_path: Path) -> None:
    from test_monitoring import frame

    store = DatasetStore(tmp_path / "synthetic.sqlite")
    instance = monitor(store)
    instance.set_connection(True, START)
    update = adopt_economic_snapshot(
        store, instance.monitor_id, snapshot(), activated_at=START, reason="synthetic review"
    )
    with store.connection(write=True) as db:
        db.execute("UPDATE monitor_economic_updates SET reason = 'changed' WHERE id = ?", (update,))
    with pytest.raises(ValueError, match="does not match its identity"):
        instance.ingest(frame(0, mode=FeedStatus.SIMULATED))
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM monitor_minutes").fetchone()[0] == 0
