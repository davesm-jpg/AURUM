from dataclasses import replace
from datetime import date, timedelta

import pytest

from aurum.economic_events import EventAssessment, EventRisk
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.execution import (
    ExecutionConfig,
    ExitReason,
    OrderEvent,
    OrderKind,
    PaperExecution,
    PaperTrade,
    UnresolvedTrade,
)
from aurum.instruments import MgcContract
from aurum.market_context import ContextSnapshot, Regime, ResearchSession
from aurum.market_data import Candle, TradePrint
from aurum.market_structure import Direction, Trend
from aurum.paper_risk import DEFAULT_RISK_CONFIG, size_paper_setup
from aurum.performance import summarize
from aurum.strategy import SetupEvent, SetupState, Variant
from aurum.timestamps import MINUTE, TradeTimestamp, minute_timestamp

CONTRACT = MgcContract("MGC-2025-04")
TRADE_DATE = date(2025, 3, 10)
START = minute_timestamp("2025-03-10T13:00Z")
CONTEXT = ContextSnapshot(
    ResearchSession.NEW_YORK,
    Regime.RANGE,
    Trend.CONSOLIDATION,
    (),
    None,
    (),
    None,
    None,
    None,
    EventAssessment(EventRisk.UNKNOWN),
)


def synthetic_calendar(
    *windows: tuple[int, int], coverage_end: int | None = None
) -> SessionCalendar:
    spans = tuple(
        TradingWindow(START + start * MINUTE, START + end * MINUTE) for start, end in windows
    )
    coverage = TradingWindow(
        spans[0].start, spans[-1].end if coverage_end is None else START + coverage_end * MINUTE
    )
    return SessionCalendar(
        "synthetic execution calendar",
        "fixture-v1",
        coverage,
        sessions=(TradingSession(TRADE_DATE, spans),),
    )


def synthetic_minute(
    index: int, open_: int = 150, high: int = 159, low: int = 140, close: int = 150
) -> Candle:
    return Candle(CONTRACT, START + index * MINUTE, open_, high, low, close, 10)


def synthetic_signal(
    candle: Candle, direction: Direction = Direction.BULLISH, setup_id: str = "synthetic-setup"
) -> SetupEvent:
    entry = candle.close_ticks
    stop = entry - 50 if direction is Direction.BULLISH else entry + 50
    plan = size_paper_setup(direction, entry, stop)
    return SetupEvent(
        setup_id,
        Variant.MSS,
        direction,
        SetupState.MSS_CONFIRMED,
        SetupState.PAPER_SIGNAL,
        candle.opened_at,
        candle.available_at,
        CONTEXT,
        None,
        None,
        None,
        stop,
        plan,
    )


@pytest.mark.parametrize("direction", list(Direction))
def test_same_bar_collision_is_conservative_and_net_costs_are_explicit(
    direction: Direction,
) -> None:
    engine = PaperExecution(synthetic_calendar((0, 4)), CONTRACT, DEFAULT_RISK_CONFIG)
    first = synthetic_minute(0, 150, 260, 39, 150)
    assert engine.advance(first) == ()
    signal = synthetic_signal(first, direction)
    assert engine.submit(signal).kind is OrderKind.QUEUED
    assert engine.submit(signal).reason == "duplicate_setup"
    entry, outcome = engine.advance(synthetic_minute(1, 150, 260, 39, 150))
    assert isinstance(entry, OrderEvent) and entry.kind is OrderKind.FILLED
    assert entry.entry_at == first.available_at
    assert isinstance(outcome, PaperTrade)
    assert outcome.reason is ExitReason.STOP
    assert outcome.ambiguous is True
    assert outcome.gross_cents == -5_000
    assert outcome.net_cents == -5_260
    assert outcome.mfe_ticks == 0
    assert outcome.mae_ticks == 50


@pytest.mark.parametrize("direction", list(Direction))
@pytest.mark.parametrize("target_first", [True, False])
def test_trade_prints_resolve_dual_touch_in_exact_source_sequence(
    direction: Direction, target_first: bool
) -> None:
    first = synthetic_minute(0)
    sign = 1 if direction is Direction.BULLISH else -1
    target_print, stop_print = 150 + 110 * sign, 150 - 60 * sign
    chosen, later = (target_print, stop_print) if target_first else (stop_print, target_print)
    prices = (150, 150 + 12 * sign, chosen, later, 150)
    candle = synthetic_minute(1, 150, max(prices), min(prices), 150)
    prints = tuple(
        TradePrint(CONTRACT, TradeTimestamp(candle.opened_at, 7), index, price, 2)
        for index, price in enumerate(prices)
    )
    execution = PaperExecution(
        synthetic_calendar((0, 4)), CONTRACT, DEFAULT_RISK_CONFIG, trade_prints=lambda _: prints
    )
    execution.advance(first)
    execution.submit(synthetic_signal(first, direction))
    _, trade = execution.advance(candle)
    assert isinstance(trade, PaperTrade)
    assert trade.ambiguous is False
    assert trade.reason is (ExitReason.TARGET if target_first else ExitReason.STOP)
    assert trade.net_cents == (9_740 if target_first else -5_260)
    assert trade.mfe_ticks == (100 if target_first else 12)
    assert trade.mae_ticks == (0 if target_first else 50)
    assert trade.bracket_touch == prints[2]
    assert trade.as_dict()["bracket_touch_print"] == prints[2].as_dict()
    assert trade.recorded_at == candle.available_at

    inconsistent = PaperExecution(
        synthetic_calendar((0, 4)),
        CONTRACT,
        DEFAULT_RISK_CONFIG,
        trade_prints=lambda _: tuple(reversed(prints)),
    )
    inconsistent.advance(first)
    inconsistent.submit(synthetic_signal(first, direction))
    with pytest.raises(ValueError, match="reconstruct the ambiguous minute"):
        inconsistent.advance(candle)


def test_latency_and_entry_recheck_do_not_use_the_late_bar_range_for_entry() -> None:
    engine = PaperExecution(
        synthetic_calendar((0, 7)),
        CONTRACT,
        DEFAULT_RISK_CONFIG,
        ExecutionConfig(latency_minutes=1),
    )
    first = synthetic_minute(0)
    engine.advance(first)
    engine.submit(synthetic_signal(first))
    assert engine.advance(synthetic_minute(1, 150, 270, 90, 160)) == ()
    (event,) = engine.advance(synthetic_minute(2, 151, 250, 100, 159))
    assert isinstance(event, OrderEvent)
    assert event.kind is OrderKind.CANCELED
    assert event.reason == "entry_risk_exceeded"


def test_observed_paper_entry_skips_an_open_before_actual_signal_availability() -> None:
    engine = PaperExecution(synthetic_calendar((0, 6)), CONTRACT, DEFAULT_RISK_CONFIG)
    first = synthetic_minute(0)
    observed = first.available_at + timedelta(seconds=1)
    engine.advance(first, observed_at=observed)
    order = engine.submit(synthetic_signal(first), observed_at=observed)
    assert order.recorded_at == observed
    assert engine.advance(synthetic_minute(1, 150, 300, 50, 160)) == ()
    actual = synthetic_minute(2, 150, 260, 140, 180)
    available = actual.available_at + timedelta(seconds=2)
    entry, exit_event = engine.advance(actual, observed_at=available)
    assert isinstance(entry, OrderEvent) and entry.entry_at == actual.opened_at
    assert entry.recorded_at == available
    assert isinstance(exit_event, PaperTrade) and exit_event.recorded_at == available
    assert exit_event.reason is ExitReason.TARGET
    assert exit_event.net_cents == 9_740


def test_untrusted_data_releases_forward_paper_state_without_marking_a_trade() -> None:
    engine = PaperExecution(synthetic_calendar((0, 6)), CONTRACT, DEFAULT_RISK_CONFIG)
    first = synthetic_minute(0)
    engine.advance(first)
    engine.submit(synthetic_signal(first))
    assert engine.pending_setup_id == "synthetic-setup"
    (fill,) = engine.advance(synthetic_minute(1))
    assert isinstance(fill, OrderEvent)
    assert engine.open_setup_id == fill.setup_id
    interrupted = START + 4 * MINUTE
    (unresolved,) = engine.interrupt(interrupted, "data_continuity_lost")
    assert isinstance(unresolved, UnresolvedTrade)
    assert unresolved.recorded_at == interrupted and unresolved.entry_at == first.available_at
    assert "net_cents" not in unresolved.as_dict()
    assert engine.open_setup_id is None and engine.pending_setup_id is None
    with pytest.raises(ValueError, match="observation clock"):
        engine.advance(synthetic_minute(2))


def test_optional_net_cap_rechecks_spread_and_never_moves_the_stop() -> None:
    risk = replace(DEFAULT_RISK_CONFIG, net_allowance_cents=5_300)
    engine = PaperExecution(
        synthetic_calendar((0, 4)), CONTRACT, risk, ExecutionConfig(round_trip_spread_ticks=1)
    )
    first = synthetic_minute(0)
    engine.advance(first)
    engine.submit(synthetic_signal(first))
    (event,) = engine.advance(synthetic_minute(1))
    assert isinstance(event, OrderEvent)
    assert event.reason == "entry_risk_exceeded"


@pytest.mark.parametrize(
    ("gap", "expected_price", "reason", "net"),
    [(87, 87, ExitReason.STOP, -6_560), (270, 250, ExitReason.TARGET, 9_740)],
)
def test_gaps_use_adverse_stop_open_and_conservative_target(
    gap: int, expected_price: int, reason: ExitReason, net: int
) -> None:
    engine = PaperExecution(synthetic_calendar((0, 5)), CONTRACT, DEFAULT_RISK_CONFIG)
    first = synthetic_minute(0)
    engine.advance(first)
    engine.submit(synthetic_signal(first))
    (entry,) = engine.advance(synthetic_minute(1))
    assert isinstance(entry, OrderEvent)
    candle = synthetic_minute(2, gap, gap + 5, gap - 5, gap)
    (outcome,) = engine.advance(candle)
    assert isinstance(outcome, PaperTrade)
    assert outcome.at_open and outcome.exit_at == candle.opened_at
    assert outcome.recorded_at == candle.available_at
    assert outcome.exit_ticks == expected_price
    assert outcome.reason is reason
    assert outcome.net_cents == net
    assert outcome.holding_seconds == 60


def test_scheduled_close_and_truncated_end_are_distinct_outcomes() -> None:
    config = DEFAULT_RISK_CONFIG
    engine = PaperExecution(synthetic_calendar((0, 2), (4, 6)), CONTRACT, config)
    first = synthetic_minute(0)
    engine.advance(first)
    engine.submit(synthetic_signal(first))
    last = synthetic_minute(1)
    _, outcome = engine.advance(last)
    assert isinstance(outcome, PaperTrade) and outcome.reason is ExitReason.WINDOW_CLOSE
    assert engine.submit(synthetic_signal(last, setup_id="other")).reason == "entry_window_closed"

    partial = PaperExecution(synthetic_calendar((0, 4), coverage_end=2), CONTRACT, config)
    partial.advance(first)
    partial.submit(synthetic_signal(first))
    partial.advance(last)
    (end,) = partial.finish()
    assert isinstance(end, PaperTrade) and end.reason is ExitReason.END_OF_DATA
    assert partial.finish() == ()


def test_performance_uses_closed_trade_equity_and_actual_sample_count() -> None:
    first = synthetic_minute(0)
    engine = PaperExecution(synthetic_calendar((0, 6)), CONTRACT, DEFAULT_RISK_CONFIG)
    engine.advance(first)
    engine.submit(synthetic_signal(first))
    _, loss = engine.advance(synthetic_minute(1, 150, 151, 95, 100))
    assert isinstance(loss, PaperTrade)
    next_close = synthetic_minute(2)
    engine.advance(next_close)
    engine.submit(synthetic_signal(next_close, setup_id="synthetic-win"))
    _, win = engine.advance(synthetic_minute(3, 150, 251, 140, 250))
    assert isinstance(win, PaperTrade)
    report = summarize([loss, win], 2_500_000)
    assert report["trades"] == 2
    assert report["wins"] == report["losses"] == 1
    assert report["gross_profit_cents"] == 10_000
    assert report["gross_loss_cents"] == 5_000
    assert report["net_profit_cents"] == 4_480
    assert report["expectancy_cents"] == 2_240
    assert report["max_drawdown_cents"] == 5_260
    assert report["ending_equity_cents"] == 2_504_480
    assert report["observation_milestones"] == {}
    empty = summarize([], 2_500_000)
    assert empty["expectancy_cents"] is None
    assert empty["win_rate_wilson_95_iid"] is None
