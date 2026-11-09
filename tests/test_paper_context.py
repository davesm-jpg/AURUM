import json
from dataclasses import replace
from datetime import date

import pytest

from aurum.candles import AggregateCandle, Timeframe
from aurum.economic_events import Announcement, EconomicCalendar, EventRisk
from aurum.exchange_calendar import TradingWindow
from aurum.instruments import MgcContract
from aurum.market_context import ContextConfig, MarketContext, PriceLocation, ResearchSession
from aurum.market_structure import (
    DEFAULT_STRUCTURE_CONFIG,
    Direction,
    Relation,
    Scope,
    Side,
    Swing,
)
from aurum.paper_risk import DEFAULT_RISK_CONFIG, RiskRejection, TargetMode, size_paper_setup
from aurum.timestamps import MINUTE, minute_timestamp

CONTRACT = MgcContract("MGC-2025-04")
START = minute_timestamp("2025-03-10T13:30Z")


@pytest.mark.parametrize("direction", list(Direction))
def test_paper_sizing_matches_reference_costs(direction: Direction) -> None:
    sign = 1 if direction is Direction.BULLISH else -1
    plan = size_paper_setup(direction, 2_900, 2_900 - sign * 50, (2_900 + sign * 70,))
    assert plan.quantity == 1
    assert plan.target_ticks == 2_900 + sign * 100
    assert plan.intervening_level_ticks == 2_900 + sign * 70
    assert plan.net_reward_cents == 9_740
    assert plan.net_loss_cents == 5_260


def test_risk_caps_keep_structural_stop_and_round_whole_quantity_down() -> None:
    plan = size_paper_setup(Direction.BULLISH, 2_900, 2_851)
    assert plan.quantity == 1
    assert plan.stop_ticks == 2_851
    assert plan.gross_risk_cents == 4_900
    assert (
        size_paper_setup(Direction.BULLISH, 2_900, 2_849).rejection
        is RiskRejection.SIZE_INCOMPATIBLE
    )
    config = replace(DEFAULT_RISK_CONFIG, net_allowance_cents=5_000)
    constrained = size_paper_setup(Direction.BULLISH, 2_900, 2_880, config=config)
    assert constrained.quantity == 2
    assert constrained.net_loss_cents == 4_520
    too_wide = size_paper_setup(Direction.BULLISH, 2_900, 2_850, config=config)
    assert too_wide.quantity == 0
    assert too_wide.stop_ticks == 2_850


def test_structural_target_is_a_separate_variant() -> None:
    config = replace(DEFAULT_RISK_CONFIG, target_mode=TargetMode.STRUCTURAL)
    plan = size_paper_setup(Direction.BULLISH, 2_900, 2_880, (2_950, 2_930, 2_890), config)
    assert plan.target_ticks == 2_930
    assert plan.intervening_level_ticks is None
    assert plan.gross_reward_cents == 6_000
    assert size_paper_setup(Direction.BULLISH, 2_900, 2_880, (), config).rejection is (
        RiskRejection.NO_STRUCTURAL_TARGET
    )


def test_economic_calendar_distinguishes_unknown_published_and_actual_times() -> None:
    event = Announcement(
        "synthetic-cpi",
        "Synthetic CPI fixture",
        START + 10 * MINUTE,
        START + 2 * MINUTE,
        START + 20 * MINUTE,
        START + 21 * MINUTE,
    )
    window = TradingWindow(START - 60 * MINUTE, START + 60 * MINUTE)
    calendar = EconomicCalendar(
        "synthetic source", "fixture-v1", window, START + 2 * MINUTE, (event,)
    )
    assert EconomicCalendar.from_json(json.dumps(calendar.as_dict())) == calendar
    assert calendar.assess(START, 15).risk is EventRisk.UNKNOWN
    assert calendar.assess(START + 3 * MINUTE, 15).risk is EventRisk.ELEVATED
    assert calendar.assess(START + 20 * MINUTE, 5).risk is EventRisk.CLEAR
    assert calendar.assess(START + 21 * MINUTE, 5).event_ids == ("synthetic-cpi",)
    assert calendar.assess(window.end - MINUTE, 5).risk is EventRisk.UNKNOWN


def test_context_keeps_structural_range_and_known_higher_availability() -> None:
    context = MarketContext(CONTRACT, DEFAULT_STRUCTURE_CONFIG, ContextConfig())
    close = START + 5 * MINUTE
    higher = AggregateCandle(
        CONTRACT,
        date(2025, 3, 10),
        Timeframe.FIFTEEN_MINUTES,
        close - 15 * MINUTE,
        close,
        100,
        105,
        95,
        102,
        15,
        15,
        False,
    )
    context.observe_higher((higher,))
    primary = AggregateCandle(
        CONTRACT,
        date(2025, 3, 10),
        Timeframe.FIVE_MINUTES,
        START,
        close,
        100,
        105,
        95,
        103,
        10,
        5,
        False,
    )
    high = Swing(Scope.INTERNAL, Side.HIGH, Relation.INITIAL, 110, START - 5 * MINUTE, START, close)
    low = Swing(Scope.INTERNAL, Side.LOW, Relation.INITIAL, 90, START - 10 * MINUTE, START, close)
    snapshot = context.observe_primary(primary, (high, low))
    assert snapshot.structural_range is not None
    assert snapshot.structural_range.location is PriceLocation.PREMIUM
    assert snapshot.higher[0].available_at == close
    assert snapshot.higher[1].available_at is None
    assert snapshot.session is ResearchSession.NEW_YORK
    assert snapshot.economic.risk is EventRisk.UNKNOWN
