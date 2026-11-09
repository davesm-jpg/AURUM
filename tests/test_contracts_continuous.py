import json
import subprocess
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from sys import executable

import pytest

from aurum.continuous import PriceBasis, SeriesPlan, SeriesSegment, replay_series, save_series
from aurum.contracts import (
    ContractActivity,
    ContractCatalog,
    ContractListing,
    RollPolicy,
    RollReason,
)
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-03-10T13:00Z")
DAY = timedelta(days=1)
FIRST = MgcContract("MGC-2025-04")
NEXT = MgcContract("MGC-2025-06")


def test_roll_decision_waits_for_comparable_and_available_activity() -> None:
    listings = (
        ContractListing(FIRST, START - 10 * DAY, START + 20 * DAY, START - 10 * DAY),
        ContractListing(NEXT, START - 4 * DAY, START + 80 * DAY, START - 4 * DAY),
    )
    activity = (
        ContractActivity(FIRST, START - DAY, START, START, 100, 100),
        ContractActivity(NEXT, START - DAY, START, START + MINUTE, 150, 150),
        ContractActivity(NEXT, START, START + DAY, START + DAY, 500),
    )
    catalog = ContractCatalog(
        "synthetic listings", "fixture-v1", DataKind.SYNTHETIC, listings, activity
    )
    assert ContractCatalog.from_json(json.dumps(catalog.as_dict())) == catalog
    assert catalog.decide(START, FIRST).reason is RollReason.KEEP
    decision = catalog.decide(START + MINUTE, FIRST)
    assert decision.selected == NEXT
    assert decision.reason is RollReason.VOLUME
    assert decision.evidence == activity[:2]
    assert catalog.decide(START + DAY, FIRST).evidence == activity[:2]
    assert catalog.decide(START + 19 * DAY, FIRST).reason is RollReason.EXPIRY
    assert catalog.decide(START + 20 * DAY, FIRST).reason is RollReason.EXPIRED
    assert catalog.decide(START + 80 * DAY, NEXT).selected is None


def test_open_interest_is_explicit_and_future_listing_is_ineligible() -> None:
    first = ContractListing(FIRST, START - DAY, START + 10 * DAY, START - DAY)
    successor = ContractListing(NEXT, START, START + 20 * DAY, START + DAY)
    activity = (
        ContractActivity(FIRST, START, START + DAY, START + DAY, open_interest=90),
        ContractActivity(NEXT, START, START + DAY, START + DAY, open_interest=120),
    )
    catalog = ContractCatalog(
        "synthetic listings", "fixture-v1", DataKind.SYNTHETIC, (first, successor), activity
    )
    assert catalog.available_at(START) == (first,)
    assert catalog.decide(START + DAY, FIRST).reason is RollReason.KEEP
    assert (
        catalog.decide(START + DAY, FIRST, RollPolicy(open_interest_lead_bps=11_000)).reason
        is RollReason.OPEN_INTEREST
    )


def _save_source(
    store: DatasetStore,
    root: Path,
    contract: MgcContract,
    price: int,
    provider: str = "synthetic provider",
    trade_date: date = date(2025, 3, 10),
) -> str:
    span = TradingWindow(START, START + 4 * MINUTE)
    calendar = SessionCalendar(
        "synthetic exchange schedule",
        "fixture-v1",
        span,
        sessions=(TradingSession(trade_date, (span,)),),
    )
    file = root / f"{contract.code}.csv"
    with file.open("w", encoding="utf-8") as stream:
        stream.write("timestamp,contract,open,high,low,close,volume\n")
        for index in range(4):
            stamp = (START + index * MINUTE).isoformat()
            stream.write(f"{stamp},{contract.code},{price},{price},{price},{price},1\n")
    config = ImportConfig(provider, contract, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC)
    return store.save(prepare_csv(file, config, calendar))


def test_continuous_forward_adjustment_preserves_actual_dated_prices(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "series.sqlite")
    old = _save_source(store, tmp_path, FIRST, 2900)
    new = _save_source(store, tmp_path, NEXT, 2910)
    switch = START + 2 * MINUTE
    left = SeriesSegment(old, TradingWindow(START, switch))
    right = SeriesSegment(new, TradingWindow(switch, START + 4 * MINUTE), switch - MINUTE)
    plan = SeriesPlan(PriceBasis.FORWARD_ADJUSTED, (left, right))
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan.as_dict()), encoding="utf-8")
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    result = subprocess.run(
        [*aurum, "continuous", str(path)], check=True, capture_output=True, text=True
    )
    series_id = json.loads(result.stdout)["report_id"]
    events = list(replay_series(store, series_id))
    assert [event["adjustment_ticks"] for event in events] == [0, 0, -100, -100]
    for event, contract, price in zip(
        events, (FIRST, FIRST, NEXT, NEXT), (29000, 29000, 29100, 29100), strict=True
    ):
        dated = event["dated_source"]
        research = event["research_ohlc_ticks"]
        assert isinstance(dated, dict) and isinstance(research, dict)
        assert dated["contract"] == contract.code
        assert dated["close_ticks"] == price
        assert research["close"] == 29000
        assert event["data_kind"] == "synthetic"

    raw = save_series(
        store, SeriesPlan(PriceBasis.UNADJUSTED, (left, replace(right, reference_open=None)))
    )
    raw_events = list(replay_series(store, raw))
    assert [event["adjustment_ticks"] for event in raw_events] == [0, 0, 0, 0]
    assert [event["dated_source"] for event in raw_events] == [
        event["dated_source"] for event in events
    ]
    assert [event["research_ohlc_ticks"] for event in raw_events[:2]] == [
        event["research_ohlc_ticks"] for event in events[:2]
    ]
    with pytest.raises(ValueError, match="previous segment"):
        SeriesPlan(PriceBasis.FORWARD_ADJUSTED, (left, replace(right, reference_open=switch)))
    mixed = _save_source(store, tmp_path, NEXT, 2910, provider="different synthetic source")
    with pytest.raises(ValueError, match="incompatible provenance"):
        save_series(
            store, SeriesPlan(PriceBasis.FORWARD_ADJUSTED, (left, replace(right, dataset_id=mixed)))
        )
    different_day = _save_source(store, tmp_path, NEXT, 2910, trade_date=date(2025, 3, 11))
    with pytest.raises(ValueError, match="calendars disagree"):
        save_series(
            store,
            SeriesPlan(
                PriceBasis.FORWARD_ADJUSTED, (left, replace(right, dataset_id=different_day))
            ),
        )
