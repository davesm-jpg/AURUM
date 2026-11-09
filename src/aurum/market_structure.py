from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from aurum.candles import AggregateCandle, Timeframe
from aurum.instruments import MgcContract
from aurum.volatility import TrueRangeWindow

STRUCTURE_VERSION = 1
BASIS_POINTS = 10_000


class Scope(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class Side(StrEnum):
    HIGH = "high"
    LOW = "low"


class Relation(StrEnum):
    INITIAL = "initial"
    HIGHER_HIGH = "higher_high"
    LOWER_HIGH = "lower_high"
    HIGHER_LOW = "higher_low"
    LOWER_LOW = "lower_low"
    EQUAL_HIGH = "equal_high"
    EQUAL_LOW = "equal_low"


class Trend(StrEnum):
    UNKNOWN = "unknown"
    BULLISH = "bullish"
    BEARISH = "bearish"
    CONSOLIDATION = "consolidation"
    TRANSITION = "transition"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class BreakMode(StrEnum):
    WICK = "wick"
    CLOSE = "close"
    BUFFERED_CLOSE = "buffered_close"
    DISPLACEMENT_CLOSE = "displacement_close"


@dataclass(frozen=True, slots=True)
class PivotRule:
    left: int = 3
    right: int = 3

    def __post_init__(self) -> None:
        if self.left < 1 or self.right < 1:
            raise ValueError("pivot left and right lengths must be positive")


@dataclass(frozen=True, slots=True)
class BreakRule:
    mode: BreakMode = BreakMode.CLOSE
    atr_period: int = 14
    atr_buffer_bps: int = 1_000
    minimum_body_atr_bps: int = 10_000
    minimum_body_range_bps: int = 6_000

    def __post_init__(self) -> None:
        if self.atr_period < 1 or min(self.atr_buffer_bps, self.minimum_body_atr_bps) < 0:
            raise ValueError("invalid ATR period or threshold")
        if not 0 <= self.minimum_body_range_bps <= BASIS_POINTS:
            raise ValueError("minimum body/range basis points must be between 0 and 10000")


@dataclass(frozen=True, slots=True)
class StructureConfig:
    internal: PivotRule = field(default_factory=PivotRule)
    external: PivotRule = field(default_factory=lambda: PivotRule(5, 5))
    breaks: BreakRule = field(default_factory=BreakRule)


DEFAULT_STRUCTURE_CONFIG = StructureConfig()


@dataclass(frozen=True, slots=True)
class Swing:
    scope: Scope
    side: Side
    relation: Relation
    price_ticks: int
    pivot_opened_at: datetime
    confirmation_opened_at: datetime
    confirmed_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "event": "swing_confirmed",
            "scope": self.scope,
            "side": self.side,
            "relation": self.relation,
            "price_ticks": self.price_ticks,
            "pivot_opened_at": self.pivot_opened_at.isoformat(),
            "confirmation_opened_at": self.confirmation_opened_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TrendChange:
    scope: Scope
    previous: Trend
    current: Trend
    source_opened_at: datetime
    confirmed_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "event": "trend_changed",
            "scope": self.scope,
            "previous": self.previous,
            "current": self.current,
            "source_opened_at": self.source_opened_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class Displacement:
    body_ticks: int
    range_ticks: int
    prior_true_range_sum: int | None
    atr_period: int

    @property
    def body_range_bps(self) -> int:
        return abs(self.body_ticks) * BASIS_POINTS // self.range_ticks if self.range_ticks else 0

    def as_dict(self) -> dict[str, object]:
        return {
            "body_ticks": self.body_ticks,
            "range_ticks": self.range_ticks,
            "body_range_bps": self.body_range_bps,
            "prior_true_range_sum": self.prior_true_range_sum,
            "atr_period": self.atr_period,
        }


@dataclass(frozen=True, slots=True)
class StructureBreak:
    direction: Direction
    mode: BreakMode
    prior: Trend
    pivot: Swing
    break_opened_at: datetime
    confirmed_at: datetime
    break_price_ticks: int
    extension_ticks: int
    displacement: Displacement
    invalidation_pivot: Swing | None = None

    def as_dict(self) -> dict[str, object]:
        invalidation = self.invalidation_pivot
        return {
            "event": "mss_confirmed" if invalidation else "bos_confirmed",
            "scope": self.pivot.scope,
            "direction": self.direction,
            "mode": self.mode,
            "prior": self.prior,
            "pivot": self.pivot.as_dict(),
            "break_opened_at": self.break_opened_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
            "break_price_ticks": self.break_price_ticks,
            "extension_ticks": self.extension_ticks,
            "displacement": self.displacement.as_dict(),
            "invalidation": {
                "pivot": invalidation.as_dict(),
                "rule": (
                    "close_at_or_below"
                    if self.direction is Direction.BULLISH
                    else "close_at_or_above"
                ),
            }
            if invalidation
            else None,
        }


type StructureEvent = Swing | TrendChange | StructureBreak


@dataclass(slots=True)
class _Level:
    swing: Swing
    broken: bool = False


class _Structure:
    def __init__(self, scope: Scope, rule: PivotRule) -> None:
        self.scope = scope
        self.rule = rule
        self.bars: deque[AggregateCandle] = deque(maxlen=rule.left + rule.right + 1)
        self.levels: dict[Side, _Level] = {}
        self.trend = Trend.UNKNOWN
        self.transition_at: datetime | None = None

    def push(
        self, bar: AggregateCandle, rule: BreakRule, displacement: Displacement
    ) -> list[StructureEvent]:
        events: list[StructureEvent] = []
        breaks = [
            event
            for direction in Direction
            if (event := self._break(bar, rule, direction, displacement)) is not None
        ]
        if len(breaks) == 1:
            event = breaks[0]
            invalidation = self._invalidation(bar, event.direction)
            if invalidation is not None:
                breaks[0] = replace(event, invalidation_pivot=invalidation)
                self.transition_at = bar.available_at
        events.extend(breaks)
        if self.transition_at == bar.available_at:
            events.append(self._change(Trend.TRANSITION, bar))

        confirmed = self._confirm(bar)
        events.extend(confirmed)
        if confirmed and self._can_classify():
            trend = self._trend_from_swings()
            if trend is not self.trend:
                events.append(self._change(trend, bar))
        return events

    def _break(
        self, bar: AggregateCandle, rule: BreakRule, direction: Direction, evidence: Displacement
    ) -> StructureBreak | None:
        bullish = direction is Direction.BULLISH
        level = self.levels.get(Side.HIGH if bullish else Side.LOW)
        if level is None or level.broken:
            return None
        price = bar.close_ticks
        if rule.mode is BreakMode.WICK:
            price = bar.high_ticks if bullish else bar.low_ticks
        extension = price - level.swing.price_ticks if bullish else level.swing.price_ticks - price
        if extension <= 0 or not _qualifies(rule, direction, extension, evidence):
            return None
        level.broken = True
        return StructureBreak(
            direction,
            rule.mode,
            self.trend,
            level.swing,
            bar.opened_at,
            bar.available_at,
            price,
            extension,
            evidence,
        )

    def _invalidation(self, bar: AggregateCandle, direction: Direction) -> Swing | None:
        if direction is Direction.BULLISH and self.trend is Trend.BEARISH:
            swing = self.levels[Side.LOW].swing
            if bar.close_ticks > swing.price_ticks:
                return swing
        if direction is Direction.BEARISH and self.trend is Trend.BULLISH:
            swing = self.levels[Side.HIGH].swing
            if bar.close_ticks < swing.price_ticks:
                return swing
        return None

    def _confirm(self, bar: AggregateCandle) -> list[Swing]:
        self.bars.append(bar)
        if len(self.bars) != self.bars.maxlen:
            return []
        pivot = self.bars[self.rule.left]
        neighbors = [item for index, item in enumerate(self.bars) if index != self.rule.left]
        swings = []
        if all(pivot.high_ticks > neighbor.high_ticks for neighbor in neighbors):
            swings.append(self._swing(bar, pivot, Side.HIGH, pivot.high_ticks))
        if all(pivot.low_ticks < neighbor.low_ticks for neighbor in neighbors):
            swings.append(self._swing(bar, pivot, Side.LOW, pivot.low_ticks))
        return swings

    def _swing(self, bar: AggregateCandle, pivot: AggregateCandle, side: Side, price: int) -> Swing:
        previous = self.levels.get(side)
        relation = _relation(side, price, previous.swing.price_ticks if previous else None)
        swing = Swing(
            self.scope, side, relation, price, pivot.opened_at, bar.opened_at, bar.available_at
        )
        self.levels[side] = _Level(swing)
        return swing

    def _can_classify(self) -> bool:
        transition_at = self.transition_at
        return transition_at is None or (
            len(self.levels) == 2
            and all(level.swing.confirmed_at > transition_at for level in self.levels.values())
        )

    def _trend_from_swings(self) -> Trend:
        if len(self.levels) < 2:
            return Trend.UNKNOWN
        high = self.levels[Side.HIGH].swing.relation
        low = self.levels[Side.LOW].swing.relation
        if Relation.INITIAL in (high, low):
            return Trend.UNKNOWN
        if high is Relation.HIGHER_HIGH and low is Relation.HIGHER_LOW:
            return Trend.BULLISH
        if high is Relation.LOWER_HIGH and low is Relation.LOWER_LOW:
            return Trend.BEARISH
        return Trend.CONSOLIDATION

    def _change(self, trend: Trend, bar: AggregateCandle) -> TrendChange:
        event = TrendChange(self.scope, self.trend, trend, bar.opened_at, bar.available_at)
        self.trend = trend
        if trend is not Trend.TRANSITION:
            self.transition_at = None
        return event


class StructureEngine:
    """Incremental market structure for one contract and timeframe."""

    def __init__(
        self,
        contract: MgcContract,
        timeframe: Timeframe,
        config: StructureConfig = DEFAULT_STRUCTURE_CONFIG,
    ) -> None:
        self.contract = contract
        self.timeframe = timeframe
        self.config = config
        self._scopes = (
            _Structure(Scope.INTERNAL, config.internal),
            _Structure(Scope.EXTERNAL, config.external),
        )
        self._previous: AggregateCandle | None = None
        self._true_ranges = TrueRangeWindow(config.breaks.atr_period)

    def push(self, bar: AggregateCandle) -> tuple[StructureEvent, ...]:
        if bar.contract != self.contract or bar.timeframe is not self.timeframe:
            raise ValueError("structure requires one contract and timeframe")
        if self._previous is not None and bar.opened_at < self._previous.available_at:
            raise ValueError("structure requires chronological, non-overlapping candle closes")

        evidence = Displacement(
            bar.close_ticks - bar.open_ticks,
            bar.high_ticks - bar.low_ticks,
            self._true_ranges.ready_sum,
            self._true_ranges.period,
        )
        events = tuple(
            event
            for scope in self._scopes
            for event in scope.push(bar, self.config.breaks, evidence)
        )
        self._true_ranges.push(bar.high_ticks, bar.low_ticks, bar.close_ticks)
        self._previous = bar
        return events


def _qualifies(
    rule: BreakRule, direction: Direction, extension: int, evidence: Displacement
) -> bool:
    if rule.mode in (BreakMode.WICK, BreakMode.CLOSE):
        return True
    atr_sum = evidence.prior_true_range_sum
    if atr_sum is None:
        return False
    if rule.mode is BreakMode.BUFFERED_CLOSE:
        return extension * evidence.atr_period * BASIS_POINTS > atr_sum * rule.atr_buffer_bps
    body = evidence.body_ticks if direction is Direction.BULLISH else -evidence.body_ticks
    return (
        body > 0
        and evidence.body_range_bps >= rule.minimum_body_range_bps
        and body * evidence.atr_period * BASIS_POINTS >= atr_sum * rule.minimum_body_atr_bps
    )


def _relation(side: Side, price: int, previous: int | None) -> Relation:
    if previous is None:
        return Relation.INITIAL
    if side is Side.HIGH:
        if price == previous:
            return Relation.EQUAL_HIGH
        return Relation.HIGHER_HIGH if price > previous else Relation.LOWER_HIGH
    if price == previous:
        return Relation.EQUAL_LOW
    return Relation.HIGHER_LOW if price > previous else Relation.LOWER_LOW
