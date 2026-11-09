from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from aurum.candles import AggregateCandle, Timeframe
from aurum.instruments import MgcContract
from aurum.market_structure import BASIS_POINTS, Direction
from aurum.volatility import TrueRangeWindow

IMBALANCE_VERSION = 1


class GapEventKind(StrEnum):
    FVG_CONFIRMED = "fvg_confirmed"
    FVG_RETESTED = "fvg_retested"
    FVG_PARTIALLY_MITIGATED = "fvg_partially_mitigated"
    FVG_FILLED = "fvg_filled"
    FVG_INVALIDATED = "fvg_invalidated"
    IFVG_CONFIRMED = "ifvg_confirmed"
    IFVG_RETESTED = "ifvg_retested"
    IFVG_REACTED = "ifvg_reacted"
    IFVG_INVALIDATED = "ifvg_invalidated"
    EXPIRED = "gap_expired"


@dataclass(frozen=True, slots=True)
class ImbalanceConfig:
    min_width_ticks: int = 1
    atr_period: int = 14
    inversion_closes: int = 1
    max_age_bars: int = 60

    def __post_init__(self) -> None:
        if min(self.min_width_ticks, self.atr_period, self.inversion_closes, self.max_age_bars) < 1:
            raise ValueError("imbalance thresholds and periods must be positive")
        if self.inversion_closes > self.max_age_bars:
            raise ValueError("inversion must be possible within the gap lifetime")


DEFAULT_IMBALANCE_CONFIG = ImbalanceConfig()


@dataclass(frozen=True, slots=True)
class GapZone:
    zone_id: str
    direction: Direction
    lower_ticks: int
    upper_ticks: int
    source_opens: tuple[datetime, datetime, datetime]
    confirmed_at: datetime
    middle_body_ticks: int
    middle_range_ticks: int
    prior_true_range_sum: int | None
    atr_period: int
    original_gap_id: str | None = None
    original_direction: Direction | None = None
    inversion_closes: int | None = None

    @property
    def width_ticks(self) -> int:
        return self.upper_ticks - self.lower_ticks

    def as_dict(self) -> dict[str, object]:
        atr_sum = self.prior_true_range_sum
        return {
            "zone_id": self.zone_id,
            "direction": self.direction,
            "lower_ticks": self.lower_ticks,
            "upper_ticks": self.upper_ticks,
            "width_ticks": self.width_ticks,
            "source_opens": [stamp.isoformat() for stamp in self.source_opens],
            "formation_opened_at": self.source_opens[1].isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
            "middle_body_ticks": self.middle_body_ticks,
            "middle_range_ticks": self.middle_range_ticks,
            "prior_true_range_sum": atr_sum,
            "atr_period": self.atr_period,
            "width_atr_bps": (
                self.width_ticks * self.atr_period * BASIS_POINTS // atr_sum if atr_sum else None
            ),
            "original_gap_id": self.original_gap_id,
            "original_direction": self.original_direction,
            "inversion_rule": "consecutive_closes_beyond_far_edge"
            if self.original_gap_id
            else None,
            "inversion_closes": self.inversion_closes,
            "imbalance_version": IMBALANCE_VERSION,
        }


@dataclass(frozen=True, slots=True)
class GapEvent:
    kind: GapEventKind
    zone: GapZone
    source_opened_at: datetime
    confirmed_at: datetime
    close_ticks: int
    mitigation_ticks: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "event": self.kind,
            "zone": self.zone.as_dict(),
            "source_opened_at": self.source_opened_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
            "close_ticks": self.close_ticks,
            "mitigation_ticks": self.mitigation_ticks,
        }


@dataclass(slots=True)
class _ActiveZone:
    zone: GapZone
    born: int
    depth: int = 0
    retested: bool = False
    reacted: bool = False
    closes_beyond: int = 0


class ImbalanceEngine:
    def __init__(
        self,
        contract: MgcContract,
        timeframe: Timeframe,
        config: ImbalanceConfig = DEFAULT_IMBALANCE_CONFIG,
    ) -> None:
        self.contract = contract
        self.timeframe = timeframe
        self.config = config
        self._recent: deque[AggregateCandle] = deque(maxlen=3)
        self._ranges = TrueRangeWindow(config.atr_period)
        self._active: list[_ActiveZone] = []
        self._index = 0

    def push(self, bar: AggregateCandle) -> tuple[GapEvent, ...]:
        if bar.contract != self.contract or bar.timeframe is not self.timeframe:
            raise ValueError("imbalances require one contract and timeframe")
        if self._recent:
            previous_close = self._recent[-1].available_at
            if bar.opened_at < previous_close:
                raise ValueError("imbalances require chronological, non-overlapping candle closes")
            if bar.opened_at > previous_close:
                self._recent.clear()

        self._index += 1
        events: list[GapEvent] = []
        active: list[_ActiveZone] = []
        for state in self._active:
            remaining = self._update(state, bar, events)
            if remaining is not None:
                active.append(remaining)

        self._recent.append(bar)
        if len(self._recent) == 3 and (zone := self._detect(*self._recent)) is not None:
            active.append(_ActiveZone(zone, self._index))
            events.append(_event(GapEventKind.FVG_CONFIRMED, zone, bar))
        self._active = active
        self._ranges.push(bar.high_ticks, bar.low_ticks, bar.close_ticks)
        return tuple(events)

    def _detect(
        self, first: AggregateCandle, middle: AggregateCandle, last: AggregateCandle
    ) -> GapZone | None:
        if last.low_ticks - first.high_ticks >= self.config.min_width_ticks:
            direction = Direction.BULLISH
            lower, upper = first.high_ticks, last.low_ticks
        elif first.low_ticks - last.high_ticks >= self.config.min_width_ticks:
            direction = Direction.BEARISH
            lower, upper = last.high_ticks, first.low_ticks
        else:
            return None
        zone_id = (
            f"{self.contract.code}/{self.timeframe}/fvg-v{IMBALANCE_VERSION}"
            f"/{last.opened_at.isoformat()}/{direction}"
        )
        return GapZone(
            zone_id,
            direction,
            lower,
            upper,
            (first.opened_at, middle.opened_at, last.opened_at),
            last.available_at,
            middle.close_ticks - middle.open_ticks,
            middle.high_ticks - middle.low_ticks,
            self._ranges.ready_sum,
            self._ranges.period,
        )

    def _update(
        self, state: _ActiveZone, bar: AggregateCandle, events: list[GapEvent]
    ) -> _ActiveZone | None:
        zone = state.zone
        if self._index - state.born > self.config.max_age_bars:
            events.append(_event(GapEventKind.EXPIRED, zone, bar))
            return None

        inverse = zone.original_gap_id is not None
        bullish = zone.direction is Direction.BULLISH
        touches = bar.low_ticks <= zone.upper_ticks and bar.high_ticks >= zone.lower_ticks
        if touches and not state.retested:
            kind = GapEventKind.IFVG_RETESTED if inverse else GapEventKind.FVG_RETESTED
            events.append(_event(kind, zone, bar))
            state.retested = True

        if touches and not inverse:
            depth = (
                zone.upper_ticks - max(zone.lower_ticks, bar.low_ticks)
                if bullish
                else min(zone.upper_ticks, bar.high_ticks) - zone.lower_ticks
            )
            if depth > state.depth:
                state.depth = depth
                kind = (
                    GapEventKind.FVG_FILLED
                    if depth == zone.width_ticks
                    else GapEventKind.FVG_PARTIALLY_MITIGATED
                )
                events.append(_event(kind, zone, bar, depth))

        invalid = (
            bar.close_ticks < zone.lower_ticks if bullish else bar.close_ticks > zone.upper_ticks
        )
        if inverse:
            if invalid:
                events.append(_event(GapEventKind.IFVG_INVALIDATED, zone, bar))
                return None
            recovers = (
                bar.close_ticks > zone.upper_ticks
                if bullish
                else bar.close_ticks < zone.lower_ticks
            )
            if touches and recovers and not state.reacted:
                events.append(_event(GapEventKind.IFVG_REACTED, zone, bar))
                state.reacted = True
            return state

        state.closes_beyond = state.closes_beyond + 1 if invalid else 0
        if state.closes_beyond < self.config.inversion_closes:
            return state
        events.append(_event(GapEventKind.FVG_INVALIDATED, zone, bar))
        inversion = replace(
            zone,
            zone_id=f"{zone.zone_id}/inverse/{bar.opened_at.isoformat()}",
            direction=Direction.BEARISH if bullish else Direction.BULLISH,
            confirmed_at=bar.available_at,
            original_gap_id=zone.zone_id,
            original_direction=zone.direction,
            inversion_closes=self.config.inversion_closes,
        )
        events.append(_event(GapEventKind.IFVG_CONFIRMED, inversion, bar))
        return _ActiveZone(inversion, self._index)


def _event(
    kind: GapEventKind, zone: GapZone, bar: AggregateCandle, depth: int | None = None
) -> GapEvent:
    return GapEvent(kind, zone, bar.opened_at, bar.available_at, bar.close_ticks, depth)
