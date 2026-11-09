from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

from aurum.candles import AggregateCandle, CandleAggregator, Timeframe
from aurum.exchange_calendar import SessionCalendar
from aurum.imbalances import DEFAULT_IMBALANCE_CONFIG, GapEvent, ImbalanceConfig, ImbalanceEngine
from aurum.instruments import MgcContract
from aurum.liquidity import (
    DEFAULT_LIQUIDITY_CONFIG,
    LiquidityConfig,
    LiquidityEngine,
    LiquidityEvent,
    LiquidityEventKind,
)
from aurum.liquidity_map import DEFAULT_LIQUIDITY_MAP_CONFIG, LiquidityMapConfig, LiquidityMapper
from aurum.market_data import Candle
from aurum.market_structure import (
    StructureBreak,
    StructureConfig,
    StructureEngine,
    StructureEvent,
    Swing,
)

EVIDENCE_VERSION = 1


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    liquidity: LiquidityConfig = DEFAULT_LIQUIDITY_CONFIG
    mapping: LiquidityMapConfig = DEFAULT_LIQUIDITY_MAP_CONFIG
    imbalances: ImbalanceConfig = DEFAULT_IMBALANCE_CONFIG


@dataclass(frozen=True, slots=True)
class SweepStructureLink:
    sweep: LiquidityEvent
    structure: StructureBreak
    elapsed_bars: int

    @property
    def confirmed_at(self) -> datetime:
        return self.structure.confirmed_at

    def as_dict(self) -> dict[str, object]:
        return {
            "event": "liquidity_sweep_mss_linked",
            "confirmed_at": self.confirmed_at.isoformat(),
            "sweep": self.sweep.as_dict(),
            "structure": self.structure.as_dict(),
            "elapsed_bars": self.elapsed_bars,
            "elapsed_seconds": int((self.confirmed_at - self.sweep.confirmed_at).total_seconds()),
        }


type EvidenceEvent = StructureEvent | LiquidityEvent | GapEvent | SweepStructureLink

DEFAULT_EVIDENCE_CONFIG = EvidenceConfig()


@dataclass(frozen=True, slots=True)
class EvidenceStep:
    bars: tuple[AggregateCandle, ...]
    analysis: AggregateCandle | None
    events: tuple[EvidenceEvent, ...]


class EvidenceEngine:
    """Emit causal structure, liquidity and imbalance events from the minute stream."""

    def __init__(
        self,
        calendar: SessionCalendar,
        contract: MgcContract,
        timeframe: Timeframe,
        structure: StructureConfig,
        config: EvidenceConfig = DEFAULT_EVIDENCE_CONFIG,
        *,
        extra_timeframes: tuple[Timeframe, ...] = (),
    ) -> None:
        frames = tuple(dict.fromkeys((timeframe, Timeframe.DAILY, *extra_timeframes)))
        self._aggregator = CandleAggregator(calendar, contract, frames)
        self._timeframe = timeframe
        self._structure = StructureEngine(contract, timeframe, structure)
        self._liquidity = LiquidityEngine(contract, timeframe, config.liquidity)
        self._mapper = LiquidityMapper(calendar, config.mapping)
        self._imbalances = ImbalanceEngine(contract, timeframe, config.imbalances)
        self._sweeps: dict[str, tuple[LiquidityEvent, int]] = {}
        self._index = 0

    def consume(self, candles: Iterable[Candle]) -> Iterator[EvidenceEvent]:
        for candle in candles:
            yield from self.push(candle)

    def push(self, candle: Candle) -> tuple[EvidenceEvent, ...]:
        step = self.step(candle)
        return step.events if step is not None else ()

    def step(self, candle: Candle) -> EvidenceStep | None:
        bars = self._aggregator.push(candle)
        self._mapper.push_minute(candle)
        analysis = None
        for bar in bars:
            if bar.timeframe is Timeframe.DAILY:
                self._mapper.push_daily(bar)
            if bar.timeframe is self._timeframe:
                analysis = bar
        if not bars:
            return None
        return EvidenceStep(bars, analysis, self._analyze(analysis) if analysis else ())

    def _analyze(self, bar: AggregateCandle) -> tuple[EvidenceEvent, ...]:
        self._index += 1
        liquidity = self._liquidity.push(bar)
        structure = self._structure.push(bar)
        events: list[EvidenceEvent] = [*liquidity, *structure]
        for event in liquidity:
            if event.sweep is None:
                continue
            if event.kind is LiquidityEventKind.SWEPT:
                self._sweeps[event.sweep.sweep_id] = (event, self._index)
            elif event.kind in (
                LiquidityEventKind.SWEEP_EXPIRED,
                LiquidityEventKind.SWEEP_INVALIDATED,
            ):
                self._sweeps.pop(event.sweep.sweep_id, None)
        for item in structure:
            if (
                isinstance(item, StructureBreak)
                and item.invalidation_pivot is not None
                and (link := self._link(item)) is not None
            ):
                events.append(link)
        events.extend(self._imbalances.push(bar))
        swings = (item for item in structure if isinstance(item, Swing))
        for level in self._mapper.release(bar, swings):
            events.extend(self._liquidity.register(level, bar.close_ticks))
        return tuple(events)

    def _link(self, structure: StructureBreak) -> SweepStructureLink | None:
        for sweep_event, index in reversed(self._sweeps.values()):
            sweep = sweep_event.sweep
            assert sweep is not None
            if (
                sweep.direction is structure.direction
                and sweep.confirmed_at < structure.confirmed_at
            ):
                return SweepStructureLink(sweep_event, structure, self._index - index)
        return None
