from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise

from aurum.candles import AggregateCandle, Timeframe
from aurum.economic_events import EconomicCalendar, EventAssessment, EventRisk
from aurum.evidence import EvidenceEvent
from aurum.instruments import MgcContract
from aurum.market_structure import (
    BASIS_POINTS,
    Direction,
    Scope,
    Side,
    StructureConfig,
    StructureEngine,
    Swing,
    Trend,
    TrendChange,
)
from aurum.sessions import ASIA_HOURS, LONDON_HOURS, SessionHours
from aurum.timestamps import MINUTE
from aurum.volatility import TrueRangeWindow

CONTEXT_VERSION = 1
CONTEXT_FRAMES = (Timeframe.FIFTEEN_MINUTES, Timeframe.ONE_HOUR, Timeframe.FOUR_HOURS)
NEW_YORK_HOURS = SessionHours("America/New_York", 8 * 60 + 30, 16 * 60)


class ResearchSession(StrEnum):
    ASIA = "asia"
    LONDON = "london"
    NEW_YORK = "new_york"
    OVERLAP = "london_new_york_overlap"
    OTHER = "other"


class PriceLocation(StrEnum):
    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"


class Regime(StrEnum):
    BULLISH = "bullish_trend"
    BEARISH = "bearish_trend"
    RANGE = "ranging"
    EXPANSION = "volatility_expansion"
    CONTRACTION = "volatility_contraction"
    THIN = "thin_liquidity"
    EVENT_RISK = "elevated_event_risk"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ContextConfig:
    asia: SessionHours = ASIA_HOURS
    london: SessionHours = LONDON_HOURS
    new_york: SessionHours = NEW_YORK_HOURS
    short_atr_period: int = 14
    baseline_period: int = 50
    efficiency_period: int = 10
    trend_efficiency_bps: int = 3_500
    expansion_bps: int = 13_000
    contraction_bps: int = 7_500
    thin_volume_bps: int = 5_000
    event_buffer_minutes: int = 15
    equilibrium_tolerance_ticks: int = 0

    def __post_init__(self) -> None:
        if not 0 < self.short_atr_period < self.baseline_period or self.efficiency_period < 1:
            raise ValueError("invalid market context lookback")
        if (
            not 0 <= self.trend_efficiency_bps <= BASIS_POINTS
            or not 0 < self.contraction_bps < BASIS_POINTS < self.expansion_bps
            or not 0 <= self.thin_volume_bps <= BASIS_POINTS
            or min(self.event_buffer_minutes, self.equilibrium_tolerance_ticks) < 0
        ):
            raise ValueError("invalid market context thresholds")


DEFAULT_CONTEXT_CONFIG = ContextConfig()


@dataclass(frozen=True, slots=True)
class FrameContext:
    timeframe: Timeframe
    trend: Trend = Trend.UNKNOWN
    available_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "timeframe": self.timeframe,
            "trend": self.trend,
            "available_at": self.available_at.isoformat() if self.available_at else None,
        }


@dataclass(frozen=True, slots=True)
class StructuralRange:
    low: Swing
    high: Swing
    location: PriceLocation

    def as_dict(self) -> dict[str, object]:
        return {
            "low": self.low.as_dict(),
            "high": self.high.as_dict(),
            "midpoint_twice_ticks": self.low.price_ticks + self.high.price_ticks,
            "location": self.location,
        }


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    session: ResearchSession
    regime: Regime
    structure: Trend
    higher: tuple[FrameContext, ...]
    structural_range: StructuralRange | None
    structural_prices: tuple[int, ...]
    volatility_ratio_bps: int | None
    volume_ratio_bps: int | None
    efficiency_bps: int | None
    economic: EventAssessment

    def aligned_frames(self, direction: Direction) -> int:
        target = Trend.BULLISH if direction is Direction.BULLISH else Trend.BEARISH
        return sum(frame.trend is target for frame in self.higher)

    def as_dict(self) -> dict[str, object]:
        return {
            "session": self.session,
            "regime": self.regime,
            "structure": self.structure,
            "higher": [frame.as_dict() for frame in self.higher],
            "structural_range": self.structural_range.as_dict() if self.structural_range else None,
            "structural_prices": self.structural_prices,
            "volatility_ratio_bps": self.volatility_ratio_bps,
            "volume_ratio_bps": self.volume_ratio_bps,
            "efficiency_bps": self.efficiency_bps,
            "economic": self.economic.as_dict(),
            "context_version": CONTEXT_VERSION,
        }


class MarketContext:
    def __init__(
        self,
        contract: MgcContract,
        structure: StructureConfig,
        config: ContextConfig = DEFAULT_CONTEXT_CONFIG,
        economic: EconomicCalendar | None = None,
    ) -> None:
        self.config = config
        self.economic = economic
        self._engines = {
            frame: StructureEngine(contract, frame, structure) for frame in CONTEXT_FRAMES
        }
        self._higher = {frame: FrameContext(frame) for frame in CONTEXT_FRAMES}
        self._pivots: dict[tuple[Scope, Side], Swing] = {}
        self._structure = Trend.UNKNOWN
        self._short = TrueRangeWindow(config.short_atr_period)
        self._long = TrueRangeWindow(config.baseline_period)
        self._volume: deque[int] = deque(maxlen=config.baseline_period)
        self._volume_sum = 0
        self._closes: deque[int] = deque(maxlen=config.efficiency_period + 1)

    def observe_higher(self, bars: Iterable[AggregateCandle]) -> None:
        for bar in bars:
            if (engine := self._engines.get(bar.timeframe)) is None:
                continue
            trend = self._higher[bar.timeframe].trend
            for event in engine.push(bar):
                if isinstance(event, TrendChange) and event.scope is Scope.INTERNAL:
                    trend = event.current
            self._higher[bar.timeframe] = FrameContext(bar.timeframe, trend, bar.available_at)

    def observe_primary(
        self, bar: AggregateCandle, events: Iterable[EvidenceEvent]
    ) -> ContextSnapshot:
        for event in events:
            if isinstance(event, TrendChange) and event.scope is Scope.INTERNAL:
                self._structure = event.current
            if isinstance(event, Swing):
                self._pivots[(event.scope, event.side)] = event

        short, long = self._short.ready_sum, self._long.ready_sum
        ratio = (
            short * self._long.period * BASIS_POINTS // (long * self._short.period)
            if short is not None and long
            else None
        )
        self._short.push(bar.high_ticks, bar.low_ticks, bar.close_ticks)
        self._long.push(bar.high_ticks, bar.low_ticks, bar.close_ticks)

        normalized_volume = bar.volume * 1_000 // bar.minute_count
        volume_ratio = (
            normalized_volume * len(self._volume) * BASIS_POINTS // self._volume_sum
            if len(self._volume) == self._volume.maxlen and self._volume_sum
            else None
        )
        if len(self._volume) == self._volume.maxlen:
            self._volume_sum -= self._volume[0]
        self._volume.append(normalized_volume)
        self._volume_sum += normalized_volume

        self._closes.append(bar.close_ticks)
        efficiency = None
        if len(self._closes) == self._closes.maxlen:
            path = sum(abs(right - left) for left, right in pairwise(self._closes))
            efficiency = (
                abs(self._closes[-1] - self._closes[0]) * BASIS_POINTS // path if path else 0
            )
        economic = (
            self.economic.assess(bar.available_at, self.config.event_buffer_minutes)
            if self.economic
            else EventAssessment(EventRisk.UNKNOWN)
        )
        higher = tuple(
            frame
            if frame.available_at is not None and frame.available_at <= bar.available_at
            else FrameContext(frame.timeframe)
            for frame in self._higher.values()
        )
        prices = tuple(sorted({pivot.price_ticks for pivot in self._pivots.values()}))
        return ContextSnapshot(
            self._session(bar.available_at - MINUTE),
            self._regime(ratio, volume_ratio, efficiency, economic.risk),
            self._structure,
            higher,
            self._range(bar.close_ticks),
            prices,
            ratio,
            volume_ratio,
            efficiency,
            economic,
        )

    def _range(self, price: int) -> StructuralRange | None:
        low = self._pivots.get((Scope.INTERNAL, Side.LOW))
        high = self._pivots.get((Scope.INTERNAL, Side.HIGH))
        if low is None or high is None or low.price_ticks >= high.price_ticks:
            return None
        distance = 2 * price - (low.price_ticks + high.price_ticks)
        if abs(distance) <= 2 * self.config.equilibrium_tolerance_ticks:
            location = PriceLocation.EQUILIBRIUM
        else:
            location = PriceLocation.PREMIUM if distance > 0 else PriceLocation.DISCOUNT
        return StructuralRange(low, high, location)

    def _session(self, stamp: datetime) -> ResearchSession:
        london = self.config.london.window_at(stamp) is not None
        new_york = self.config.new_york.window_at(stamp) is not None
        if london and new_york:
            return ResearchSession.OVERLAP
        if new_york:
            return ResearchSession.NEW_YORK
        if london:
            return ResearchSession.LONDON
        if self.config.asia.window_at(stamp) is not None:
            return ResearchSession.ASIA
        return ResearchSession.OTHER

    def _regime(
        self,
        volatility: int | None,
        volume: int | None,
        efficiency: int | None,
        event_risk: EventRisk,
    ) -> Regime:
        if event_risk is EventRisk.ELEVATED:
            return Regime.EVENT_RISK
        if volume is not None and volume <= self.config.thin_volume_bps:
            return Regime.THIN
        if volatility is not None and volatility >= self.config.expansion_bps:
            return Regime.EXPANSION
        if volatility is not None and volatility <= self.config.contraction_bps:
            return Regime.CONTRACTION
        if efficiency is None:
            return Regime.UNCERTAIN
        if efficiency >= self.config.trend_efficiency_bps:
            if self._structure is Trend.BULLISH:
                return Regime.BULLISH
            if self._structure is Trend.BEARISH:
                return Regime.BEARISH
        return Regime.RANGE if self._structure is not Trend.UNKNOWN else Regime.UNCERTAIN
