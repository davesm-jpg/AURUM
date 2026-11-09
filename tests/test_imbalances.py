from datetime import date

import pytest

from aurum.candles import AggregateCandle, Timeframe
from aurum.imbalances import GapEvent, GapEventKind, ImbalanceConfig, ImbalanceEngine
from aurum.instruments import MgcContract
from aurum.market_structure import Direction
from aurum.timestamps import MINUTE, minute_timestamp

CONTRACT = MgcContract("MGC-2025-04")
TIMEFRAME = Timeframe.FIVE_MINUTES
START = minute_timestamp("2025-03-10T13:00Z")
# Synthetic bars create a bullish gap, fill it, then close beyond it and retest from below.
PATTERN = [
    (90, 100, 96),
    (98, 115, 111),
    (107, 118, 112),
    (104, 116, 111),
    (98, 112, 102),
    (92, 104, 99),
    (94, 102, 98),
    (102, 113, 110),
]


def synthetic_bar(
    index: int, low: int, high: int, close: int, *, mirror: bool = False
) -> AggregateCandle:
    opened_at = START + 5 * index * MINUTE
    open_ = (low + high) // 2
    if mirror:
        low, high, close, open_ = 300 - high, 300 - low, 300 - close, 300 - open_
    return AggregateCandle(
        CONTRACT,
        date(2025, 3, 10),
        TIMEFRAME,
        opened_at,
        opened_at + 5 * MINUTE,
        open_,
        high,
        low,
        close,
        10,
        5,
        False,
    )


def first_gap(events: list[GapEvent]) -> list[GapEvent]:
    zone = next(event.zone for event in events if event.kind is GapEventKind.FVG_CONFIRMED)
    return [
        event
        for event in events
        if zone.zone_id in (event.zone.zone_id, event.zone.original_gap_id)
    ]


@pytest.mark.parametrize("mirror", [False, True])
def test_mirrored_gap_lifecycle_has_actual_confirmation_times(mirror: bool) -> None:
    bars = [synthetic_bar(index, *values, mirror=mirror) for index, values in enumerate(PATTERN)]
    engine = ImbalanceEngine(CONTRACT, TIMEFRAME, ImbalanceConfig(atr_period=2))
    assert engine.push(bars[0]) == ()
    assert engine.push(bars[1]) == ()
    events = first_gap([event for bar in bars[2:] for event in engine.push(bar)])
    assert [event.kind for event in events] == [
        GapEventKind.FVG_CONFIRMED,
        GapEventKind.FVG_RETESTED,
        GapEventKind.FVG_PARTIALLY_MITIGATED,
        GapEventKind.FVG_FILLED,
        GapEventKind.FVG_INVALIDATED,
        GapEventKind.IFVG_CONFIRMED,
        GapEventKind.IFVG_RETESTED,
        GapEventKind.IFVG_REACTED,
        GapEventKind.IFVG_INVALIDATED,
    ]
    gap = events[0].zone
    assert (gap.lower_ticks, gap.upper_ticks) == ((193, 200) if mirror else (100, 107))
    assert gap.source_opens == tuple(bar.opened_at for bar in bars[:3])
    assert gap.confirmed_at == bars[2].available_at
    assert gap.prior_true_range_sum == 29
    assert events[2].mitigation_ticks == 3
    assert events[3].mitigation_ticks == gap.width_ticks
    inverse = events[5].zone
    assert inverse.original_gap_id == gap.zone_id
    assert inverse.original_direction is gap.direction
    assert inverse.direction is (Direction.BULLISH if mirror else Direction.BEARISH)
    assert inverse.confirmed_at == bars[5].available_at
    assert events[6].confirmed_at == bars[6].available_at
    assert events[7].confirmed_at == bars[6].available_at
    assert events[8].confirmed_at == bars[7].available_at


def test_filter_rejects_zero_width_narrow_and_nonadjacent_patterns() -> None:
    config = ImbalanceConfig(min_width_ticks=8)
    engine = ImbalanceEngine(CONTRACT, TIMEFRAME, config)
    for index, values in enumerate(PATTERN[:3]):
        assert engine.push(synthetic_bar(index, *values)) == ()

    engine = ImbalanceEngine(CONTRACT, TIMEFRAME)
    assert engine.push(synthetic_bar(0, *PATTERN[0])) == ()
    assert engine.push(synthetic_bar(1, *PATTERN[1])) == ()
    assert engine.push(synthetic_bar(2, 100, 117, 111)) == ()

    engine = ImbalanceEngine(CONTRACT, TIMEFRAME)
    assert engine.push(synthetic_bar(0, *PATTERN[0])) == ()
    assert engine.push(synthetic_bar(1, *PATTERN[1])) == ()
    assert engine.push(synthetic_bar(3, *PATTERN[2])) == ()


def test_inverse_requires_configured_consecutive_strict_closes() -> None:
    bars = [
        *(synthetic_bar(index, *values) for index, values in enumerate(PATTERN[:6])),
        synthetic_bar(6, 95, 109, 100),
        synthetic_bar(7, 92, 104, 99),
        synthetic_bar(8, 91, 105, 98),
    ]
    engine = ImbalanceEngine(CONTRACT, TIMEFRAME, ImbalanceConfig(inversion_closes=2))
    events = first_gap([event for bar in bars for event in engine.push(bar)])
    inversions = [event for event in events if event.kind is GapEventKind.IFVG_CONFIRMED]
    (inverse,) = inversions
    assert inverse.confirmed_at == bars[8].available_at
    assert inverse.zone.inversion_closes == 2


def test_expiry_and_historical_prefix_are_deterministic() -> None:
    bars = [synthetic_bar(index, *values) for index, values in enumerate(PATTERN)]
    config = ImbalanceConfig(max_age_bars=1)
    prefix = ImbalanceEngine(CONTRACT, TIMEFRAME, config)
    expected = [event for bar in bars[:3] for event in prefix.push(bar)]
    full = ImbalanceEngine(CONTRACT, TIMEFRAME, config)
    events = [event for bar in bars for event in full.push(bar)]
    assert [event for event in events if event.confirmed_at <= bars[2].available_at] == expected
    lifetime = first_gap(events)
    assert lifetime[-1].kind is GapEventKind.EXPIRED
    assert lifetime[-1].confirmed_at == bars[4].available_at
    assert all(event.kind is not GapEventKind.IFVG_CONFIRMED for event in lifetime)
