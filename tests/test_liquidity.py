from dataclasses import replace
from datetime import date

import pytest

from aurum.candles import AggregateCandle, Timeframe
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EvidenceEngine
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.liquidity import (
    LevelKind,
    LiquidityConfig,
    LiquidityEngine,
    LiquidityEvent,
    LiquidityEventKind,
    LiquidityLevel,
)
from aurum.liquidity_map import LiquidityMapper
from aurum.market_data import Candle
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG, Direction, Relation, Scope, Side, Swing
from aurum.sessions import LONDON_HOURS, SessionHours
from aurum.timestamps import MINUTE, minute_timestamp

CONTRACT = MgcContract("MGC-2025-04")
TRADE_DATE = date(2025, 3, 10)
START = minute_timestamp("2025-03-10T13:00Z")


def synthetic_bar(
    index: int, low: int, high: int, close: int, *, mirror: bool = False
) -> AggregateCandle:
    opened = START + 5 * index * MINUTE
    open_ = (low + high) // 2
    if mirror:
        low, high, open_, close = 300 - high, 300 - low, 300 - open_, 300 - close
    return AggregateCandle(
        CONTRACT,
        TRADE_DATE,
        Timeframe.FIVE_MINUTES,
        opened,
        opened + 5 * MINUTE,
        open_,
        high,
        low,
        close,
        10,
        5,
        False,
    )


def synthetic_level(mirror: bool = False) -> LiquidityLevel:
    return LiquidityLevel(
        CONTRACT,
        LevelKind.SWING,
        Side.HIGH if mirror else Side.LOW,
        200 if mirror else 100,
        START + 5 * MINUTE,
        START - 5 * MINUTE,
        START,
        "5m",
        (START - 5 * MINUTE,),
    )


@pytest.mark.parametrize("mirror", [False, True])
def test_liquidity_requires_known_level_and_tracks_delayed_reclaim(mirror: bool) -> None:
    engine = LiquidityEngine(CONTRACT, Timeframe.FIVE_MINUTES)
    level = synthetic_level(mirror)
    (mapped,) = engine.register(level, 200 if mirror else 100)
    assert mapped.kind is LiquidityEventKind.MAPPED
    assert engine.register(level, 200 if mirror else 100) == ()
    assert engine.push(synthetic_bar(0, 90, 108, 104, mirror=mirror)) == ()

    penetration = engine.push(synthetic_bar(1, 96, 106, 98, mirror=mirror))
    assert [event.kind for event in penetration] == [
        LiquidityEventKind.TESTED,
        LiquidityEventKind.PENETRATED,
    ]
    reclaimed = engine.push(synthetic_bar(2, 94, 110, 105, mirror=mirror))
    (event,) = reclaimed
    assert event.kind is LiquidityEventKind.SWEPT
    assert event.test_count == 1
    sweep = event.sweep
    assert sweep is not None
    assert sweep.direction is (Direction.BEARISH if mirror else Direction.BULLISH)
    assert sweep.penetration_ticks == 6
    assert sweep.reclaim_ticks == 5
    assert sweep.observed_bars == 2
    assert sweep.started_at == START + 5 * MINUTE
    assert sweep.confirmed_at == START + 15 * MINUTE

    (follows,) = engine.push(synthetic_bar(3, 103, 117, 113, mirror=mirror))
    assert follows.kind is LiquidityEventKind.FOLLOW_THROUGH
    assert engine.push(synthetic_bar(4, 105, 118, 115, mirror=mirror)) == ()
    (invalidated,) = engine.push(synthetic_bar(5, 93, 104, 94, mirror=mirror))
    assert invalidated.kind is LiquidityEventKind.SWEEP_INVALIDATED
    assert invalidated.sweep is sweep


@pytest.mark.parametrize(
    ("close", "kind"),
    [(98, LiquidityEventKind.SUSTAINED_BREAK), (100, LiquidityEventKind.RECLAIM_UNCONFIRMED)],
)
def test_reclaim_deadline_distinguishes_closed_break_and_boundary(
    close: int, kind: LiquidityEventKind
) -> None:
    config = LiquidityConfig(max_reclaim_bars=1, reclaim_buffer_ticks=2)
    engine = LiquidityEngine(CONTRACT, Timeframe.FIVE_MINUTES, config)
    engine.push(synthetic_bar(0, 101, 109, 106))
    engine.register(synthetic_level(), 106)
    engine.push(synthetic_bar(1, 97, 108, 99))
    (event,) = engine.push(synthetic_bar(2, 95, 105, close))
    assert event.kind is kind
    assert engine.push(synthetic_bar(3, 96, 110, 105)) == ()


def test_same_bar_reclaim_is_eligible_only_after_registration() -> None:
    engine = LiquidityEngine(CONTRACT, Timeframe.FIVE_MINUTES, LiquidityConfig(max_reclaim_bars=0))
    first = synthetic_bar(0, 97, 108, 104)
    assert engine.push(first) == ()
    engine.register(synthetic_level(), first.close_ticks)
    events = engine.push(synthetic_bar(1, 98, 108, 104))
    (swept,) = [event for event in events if event.kind is LiquidityEventKind.SWEPT]
    assert swept.sweep is not None
    assert swept.sweep.observed_bars == 1


def synthetic_minutes(span: TradingWindow, base: int) -> list[Candle]:
    return [
        Candle(CONTRACT, span.start + index * MINUTE, base, base + index, base - index, base, 2)
        for index in range((span.end - span.start) // MINUTE)
    ]


def test_session_levels_wait_for_complete_minutes_and_analysis_close() -> None:
    span = TradingWindow(START, START + 10 * MINUTE)
    calendar = SessionCalendar(
        "synthetic session proxy",
        "fixture-v1",
        span,
        sessions=(TradingSession(TRADE_DATE, (span,)),),
    )
    config = replace(
        DEFAULT_EVIDENCE_CONFIG,
        mapping=replace(
            DEFAULT_EVIDENCE_CONFIG.mapping,
            asia_hours=SessionHours("UTC", 13 * 60 + 1, 13 * 60 + 4),
        ),
    )
    engine = EvidenceEngine(
        calendar, CONTRACT, Timeframe.FIVE_MINUTES, DEFAULT_STRUCTURE_CONFIG, config
    )
    minutes = synthetic_minutes(span, 100)
    for candle in minutes[:4]:
        assert engine.push(candle) == ()
    levels = [
        event.level
        for event in engine.push(minutes[4])
        if isinstance(event, LiquidityEvent) and event.level.kind is LevelKind.ASIA
    ]
    assert [(level.side, level.price_ticks) for level in levels] == [
        (Side.HIGH, 103),
        (Side.LOW, 97),
    ]
    assert all(level.confirmed_at == START + 5 * MINUTE for level in levels)
    assert all(level.source_end == START + 4 * MINUTE for level in levels)

    engine = EvidenceEngine(
        calendar, CONTRACT, Timeframe.FIVE_MINUTES, DEFAULT_STRUCTURE_CONFIG, config
    )
    clipped = [candle for index, candle in enumerate(minutes) if index != 2]
    assert not any(
        isinstance(event, LiquidityEvent) and event.level.kind is LevelKind.ASIA
        for event in engine.consume(clipped)
    )


def test_regional_hours_use_london_dst_and_handle_local_overnight() -> None:
    before = LONDON_HOURS.window_at(minute_timestamp("2025-03-10T08:00Z"))
    after = LONDON_HOURS.window_at(minute_timestamp("2025-03-31T07:00Z"))
    assert before is not None and before.start.hour == 8
    assert after is not None and after.start.hour == 7
    overnight = SessionHours("UTC", 23 * 60, 60)
    window = overnight.window_at(minute_timestamp("2025-03-10T00:12Z"))
    assert window is not None
    assert window.start == minute_timestamp("2025-03-09T23:00Z")
    assert window.end == minute_timestamp("2025-03-10T01:00Z")


@pytest.mark.parametrize("complete_coverage", [False, True])
def test_prior_week_needs_full_coverage_and_all_scheduled_daily_closes(
    complete_coverage: bool,
) -> None:
    monday = TradingWindow(START, START + 5 * MINUTE)
    friday_start = minute_timestamp("2025-03-14T13:00Z")
    friday = TradingWindow(friday_start, friday_start + 5 * MINUTE)
    coverage = (
        TradingWindow(
            minute_timestamp("2025-03-09T00:00-06:00"), minute_timestamp("2025-03-16T00:00-05:00")
        )
        if complete_coverage
        else TradingWindow(monday.start, friday.end)
    )
    calendar = SessionCalendar(
        "synthetic week with closed intervening trade dates",
        "fixture-v1",
        coverage,
        sessions=(
            TradingSession(TRADE_DATE, (monday,)),
            TradingSession(date(2025, 3, 14), (friday,)),
        ),
    )
    engine = EvidenceEngine(calendar, CONTRACT, Timeframe.FIVE_MINUTES, DEFAULT_STRUCTURE_CONFIG)
    events = [
        event
        for event in engine.consume(
            [*synthetic_minutes(monday, 100), *synthetic_minutes(friday, 111)]
        )
        if isinstance(event, LiquidityEvent) and event.kind is LiquidityEventKind.MAPPED
    ]
    days = [event for event in events if event.level.kind is LevelKind.PREVIOUS_DAY]
    assert [event.confirmed_at for event in days] == [
        monday.end,
        monday.end,
        friday.end,
        friday.end,
    ]
    weeks = [event for event in events if event.level.kind is LevelKind.PREVIOUS_WEEK]
    if complete_coverage:
        assert [(event.level.price_ticks, event.confirmed_at) for event in weeks] == [
            (115, friday.end),
            (96, friday.end),
        ]
    else:
        assert weeks == []


def test_confirmed_swings_form_equal_and_repeated_proxies_and_range_edges() -> None:
    span = TradingWindow(START, START + 20 * MINUTE)
    calendar = SessionCalendar(
        "synthetic price proxies",
        "fixture-v1",
        span,
        sessions=(TradingSession(TRADE_DATE, (span,)),),
    )
    config = replace(DEFAULT_EVIDENCE_CONFIG.mapping, range_bars=2)
    mapper = LiquidityMapper(calendar, config)
    seen: list[LiquidityLevel] = []
    for index, high in enumerate([111, 112, 111]):
        bar = synthetic_bar(index, 99, high, 104)
        swing = Swing(
            Scope.INTERNAL,
            Side.HIGH,
            Relation.INITIAL,
            high,
            bar.opened_at - 5 * MINUTE,
            bar.opened_at,
            bar.available_at,
        )
        seen.extend(mapper.release(bar, (swing,)))
    equal = [level for level in seen if level.kind is LevelKind.EQUAL]
    assert [(level.price_ticks, level.initial_tests) for level in equal] == [(112, 2)]
    repeated = [level for level in seen if level.kind is LevelKind.REPEATED]
    assert [(level.price_ticks, level.initial_tests) for level in repeated] == [(112, 3)]
    assert len(repeated[0].source_opens) == 3
    ranges = [level for level in seen if level.kind is LevelKind.RANGE]
    assert [(level.side, level.price_ticks) for level in ranges] == [
        (Side.HIGH, 112),
        (Side.LOW, 99),
    ]
