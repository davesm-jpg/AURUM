from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from aurum.candles import AggregateCandle, Timeframe
from aurum.instruments import MgcContract
from aurum.market_data import canonical_json
from aurum.market_structure import Direction, Side

LIQUIDITY_VERSION = 1


class LevelKind(StrEnum):
    SWING = "swing"
    EQUAL = "equal"
    REPEATED = "repeated"
    RANGE = "range"
    PREVIOUS_DAY = "previous_day"
    PREVIOUS_WEEK = "previous_week"
    ASIA = "asia"
    LONDON = "london"


class LiquidityEventKind(StrEnum):
    MAPPED = "liquidity_level_mapped"
    TESTED = "liquidity_level_tested"
    SUPERSEDED = "liquidity_level_superseded"
    LEVEL_EXPIRED = "liquidity_level_expired"
    PENETRATED = "liquidity_penetrated"
    SWEPT = "liquidity_sweep_confirmed"
    SUSTAINED_BREAK = "liquidity_sustained_break"
    RECLAIM_UNCONFIRMED = "liquidity_reclaim_unconfirmed"
    FOLLOW_THROUGH = "liquidity_follow_through"
    SWEEP_INVALIDATED = "liquidity_sweep_invalidated"
    SWEEP_EXPIRED = "liquidity_sweep_expired"


@dataclass(frozen=True, slots=True)
class LiquidityConfig:
    min_penetration_ticks: int = 1
    reclaim_buffer_ticks: int = 1
    max_reclaim_bars: int = 2
    max_level_age_bars: int = 120
    max_sweep_age_bars: int = 20

    def __post_init__(self) -> None:
        if (
            min(
                self.min_penetration_ticks,
                self.reclaim_buffer_ticks,
                self.max_level_age_bars,
                self.max_sweep_age_bars,
            )
            < 1
            or self.max_reclaim_bars < 0
        ):
            raise ValueError("invalid liquidity thresholds or lifetimes")


DEFAULT_LIQUIDITY_CONFIG = LiquidityConfig()
_ROLLING_LEVELS = frozenset(
    {
        LevelKind.RANGE,
        LevelKind.PREVIOUS_DAY,
        LevelKind.PREVIOUS_WEEK,
        LevelKind.ASIA,
        LevelKind.LONDON,
    }
)


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    contract: MgcContract
    kind: LevelKind
    side: Side
    price_ticks: int
    confirmed_at: datetime
    source_start: datetime
    source_end: datetime
    source_timeframe: str
    source_opens: tuple[datetime, ...]
    initial_tests: int = 0
    level_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.price_ticks < 1 or self.initial_tests < 0:
            raise ValueError("invalid liquidity price or test count")
        if any(
            stamp.tzinfo is not UTC
            for stamp in (self.source_start, self.source_end, self.confirmed_at, *self.source_opens)
        ):
            raise ValueError("liquidity timestamps must be UTC")
        if not self.source_start < self.source_end <= self.confirmed_at:
            raise ValueError("liquidity source period must end by confirmation")
        if not self.source_opens or any(
            not self.source_start <= stamp < self.source_end for stamp in self.source_opens
        ):
            raise ValueError("liquidity source candles must be within the source period")
        identity = canonical_json(self._identity())
        object.__setattr__(self, "level_id", sha256(identity.encode()).hexdigest())

    def _identity(self) -> dict[str, object]:
        return {
            "contract": self.contract.code,
            "kind": self.kind,
            "side": self.side,
            "price_ticks": self.price_ticks,
            "confirmed_at": self.confirmed_at.isoformat(),
            "source_start": self.source_start.isoformat(),
            "source_end": self.source_end.isoformat(),
            "source_timeframe": self.source_timeframe,
            "source_opens": [stamp.isoformat() for stamp in self.source_opens],
            "initial_tests": self.initial_tests,
            "liquidity_version": LIQUIDITY_VERSION,
        }

    def as_dict(self) -> dict[str, object]:
        return {"level_id": self.level_id, **self._identity()}


@dataclass(frozen=True, slots=True)
class Sweep:
    sweep_id: str
    direction: Direction
    started_at: datetime
    confirmed_at: datetime
    extreme_ticks: int
    penetration_ticks: int
    reclaim_ticks: int
    observed_bars: int
    body_ticks: int
    follow_through_ticks: int

    def as_dict(self) -> dict[str, object]:
        return {
            "sweep_id": self.sweep_id,
            "direction": self.direction,
            "started_at": self.started_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
            "extreme_ticks": self.extreme_ticks,
            "penetration_ticks": self.penetration_ticks,
            "reclaim_ticks": self.reclaim_ticks,
            "observed_bars": self.observed_bars,
            "observation_window_seconds": int(
                (self.confirmed_at - self.started_at).total_seconds()
            ),
            "reclaim_type": "same_bar" if self.observed_bars == 1 else "later_close",
            "body_ticks": self.body_ticks,
            "follow_through_ticks": self.follow_through_ticks,
        }


@dataclass(frozen=True, slots=True)
class LiquidityEvent:
    kind: LiquidityEventKind
    level: LiquidityLevel
    confirmed_at: datetime
    test_count: int
    source_opened_at: datetime | None = None
    distance_ticks: int | None = None
    extreme_ticks: int | None = None
    sweep: Sweep | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "event": self.kind,
            "level": self.level.as_dict(),
            "confirmed_at": self.confirmed_at.isoformat(),
            "test_count": self.test_count,
            "source_opened_at": (
                self.source_opened_at.isoformat() if self.source_opened_at else None
            ),
            "distance_ticks": self.distance_ticks,
            "extreme_ticks": self.extreme_ticks,
            "sweep": self.sweep.as_dict() if self.sweep else None,
        }


@dataclass(slots=True)
class _Pending:
    first_open: datetime
    index: int
    extreme: int


@dataclass(slots=True)
class _LevelWatch:
    level: LiquidityLevel
    tests: int
    index: int | None = None
    touching: bool = False
    pending: _Pending | None = None


@dataclass(slots=True)
class _SweepWatch:
    level: LiquidityLevel
    sweep: Sweep
    index: int
    tests: int
    followed: bool = False


class LiquidityEngine:
    """Observe price proxies after their confirmation and track closed-bar reclaims."""

    def __init__(
        self,
        contract: MgcContract,
        timeframe: Timeframe,
        config: LiquidityConfig = DEFAULT_LIQUIDITY_CONFIG,
    ) -> None:
        self.contract = contract
        self.timeframe = timeframe
        self.config = config
        self._levels: dict[str, _LevelWatch] = {}
        self._rolling: dict[tuple[LevelKind, Side], str] = {}
        self._sweeps: list[_SweepWatch] = []
        self._index = 0
        self._last_close: datetime | None = None

    def register(self, level: LiquidityLevel, close_ticks: int) -> tuple[LiquidityEvent, ...]:
        if level.contract != self.contract:
            raise ValueError("liquidity level belongs to a different contract")
        if self._last_close is not None and level.confirmed_at != self._last_close:
            raise ValueError("register liquidity at the latest analysis close")
        if level.level_id in self._levels:
            return ()
        events = []
        if level.kind in _ROLLING_LEVELS:
            key = (level.kind, level.side)
            previous_id = self._rolling.get(key)
            if previous_id is not None and (previous := self._levels.pop(previous_id, None)):
                events.append(
                    LiquidityEvent(
                        LiquidityEventKind.SUPERSEDED,
                        previous.level,
                        level.confirmed_at,
                        previous.tests,
                        distance_ticks=abs(close_ticks - previous.level.price_ticks),
                    )
                )
            self._rolling[key] = level.level_id
        self._levels[level.level_id] = _LevelWatch(level, level.initial_tests)
        events.append(
            LiquidityEvent(
                LiquidityEventKind.MAPPED,
                level,
                level.confirmed_at,
                level.initial_tests,
                distance_ticks=abs(close_ticks - level.price_ticks),
            )
        )
        return tuple(events)

    def push(self, bar: AggregateCandle) -> tuple[LiquidityEvent, ...]:
        if bar.contract != self.contract or bar.timeframe is not self.timeframe:
            raise ValueError("liquidity requires one contract and timeframe")
        if self._last_close is not None and bar.opened_at < self._last_close:
            raise ValueError("liquidity requires chronological, non-overlapping candle closes")
        self._index += 1
        events: list[LiquidityEvent] = []
        self._update_sweeps(bar, events)
        retired = [
            key
            for key, watch in self._levels.items()
            if watch.level.confirmed_at <= bar.opened_at and self._observe_level(watch, bar, events)
        ]
        for key in retired:
            del self._levels[key]
        self._last_close = bar.available_at
        return tuple(events)

    def _observe_level(
        self, watch: _LevelWatch, bar: AggregateCandle, events: list[LiquidityEvent]
    ) -> bool:
        level = watch.level
        price = level.price_ticks
        if watch.index is None:
            watch.index = self._index
        if self._index - watch.index >= self.config.max_level_age_bars:
            events.append(_event(LiquidityEventKind.LEVEL_EXPIRED, level, watch.tests, bar))
            return True

        touching = bar.low_ticks <= price <= bar.high_ticks
        if touching and not watch.touching:
            watch.tests += 1
            events.append(_event(LiquidityEventKind.TESTED, level, watch.tests, bar))
        watch.touching = touching

        bullish = level.side is Side.LOW
        extreme = bar.low_ticks if bullish else bar.high_ticks
        penetration = price - extreme if bullish else extreme - price
        if watch.pending is None and penetration >= self.config.min_penetration_ticks:
            watch.pending = _Pending(bar.opened_at, self._index, extreme)
            events.append(_event(LiquidityEventKind.PENETRATED, level, watch.tests, bar, extreme))
        pending = watch.pending
        if pending is None:
            return False

        pending.extreme = (
            min(pending.extreme, extreme) if bullish else max(pending.extreme, extreme)
        )
        reclaim = bar.close_ticks - price if bullish else price - bar.close_ticks
        elapsed = self._index - pending.index
        if reclaim >= self.config.reclaim_buffer_ticks:
            sweep = Sweep(
                f"{level.level_id}/{pending.first_open.isoformat()}",
                Direction.BULLISH if bullish else Direction.BEARISH,
                pending.first_open,
                bar.available_at,
                pending.extreme,
                abs(pending.extreme - price),
                reclaim,
                elapsed + 1,
                bar.close_ticks - bar.open_ticks,
                bar.high_ticks if bullish else bar.low_ticks,
            )
            self._sweeps.append(_SweepWatch(level, sweep, self._index, watch.tests))
            events.append(
                _event(LiquidityEventKind.SWEPT, level, watch.tests, bar, pending.extreme, sweep)
            )
            return True
        if elapsed >= self.config.max_reclaim_bars:
            kind = (
                LiquidityEventKind.SUSTAINED_BREAK
                if reclaim < 0
                else LiquidityEventKind.RECLAIM_UNCONFIRMED
            )
            events.append(_event(kind, level, watch.tests, bar, pending.extreme))
            return True
        return False

    def _update_sweeps(self, bar: AggregateCandle, events: list[LiquidityEvent]) -> None:
        active = []
        for watch in self._sweeps:
            sweep = watch.sweep
            bullish = sweep.direction is Direction.BULLISH
            invalidated = (
                bar.close_ticks <= sweep.extreme_ticks
                if bullish
                else bar.close_ticks >= sweep.extreme_ticks
            )
            follows = (
                bar.close_ticks > sweep.follow_through_ticks
                if bullish
                else bar.close_ticks < sweep.follow_through_ticks
            )
            kind = None
            if self._index - watch.index > self.config.max_sweep_age_bars:
                kind = LiquidityEventKind.SWEEP_EXPIRED
            elif invalidated:
                kind = LiquidityEventKind.SWEEP_INVALIDATED
            else:
                if follows and not watch.followed:
                    kind = LiquidityEventKind.FOLLOW_THROUGH
                    watch.followed = True
                active.append(watch)
            if kind is not None:
                events.append(
                    _event(kind, watch.level, watch.tests, bar, sweep.extreme_ticks, sweep)
                )
        self._sweeps = active


def _event(
    kind: LiquidityEventKind,
    level: LiquidityLevel,
    tests: int,
    bar: AggregateCandle,
    extreme: int | None = None,
    sweep: Sweep | None = None,
) -> LiquidityEvent:
    return LiquidityEvent(
        kind,
        level,
        bar.available_at,
        tests,
        bar.opened_at,
        abs(bar.close_ticks - level.price_ticks),
        extreme,
        sweep,
    )
