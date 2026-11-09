from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256

from aurum.candles import AggregateCandle, Timeframe
from aurum.economic_events import EconomicCalendar, EventRisk
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EvidenceConfig, EvidenceEngine, EvidenceEvent
from aurum.exchange_calendar import SessionCalendar
from aurum.imbalances import GapEvent, GapEventKind, GapZone
from aurum.instruments import MgcContract
from aurum.liquidity import LiquidityEvent, LiquidityEventKind
from aurum.market_context import (
    CONTEXT_FRAMES,
    DEFAULT_CONTEXT_CONFIG,
    ContextConfig,
    ContextSnapshot,
    MarketContext,
    Regime,
    ResearchSession,
)
from aurum.market_data import Candle, canonical_json
from aurum.market_structure import Direction, Scope, StructureBreak, StructureConfig
from aurum.paper_risk import DEFAULT_RISK_CONFIG, RiskConfig, RiskPlan, size_paper_setup

STRATEGY_VERSION = 1


class Variant(StrEnum):
    MSS = "mss"
    SWEEP_MSS = "sweep_mss"
    MSS_FVG = "mss_fvg"
    MSS_IFVG = "mss_ifvg"
    SWEEP_MSS_FVG = "sweep_mss_fvg"
    SWEEP_MSS_IFVG = "sweep_mss_ifvg"
    SWEEP_MSS_IFVG_HTF = "sweep_mss_ifvg_htf"

    @property
    def needs_sweep(self) -> bool:
        return self in (
            Variant.SWEEP_MSS,
            Variant.SWEEP_MSS_FVG,
            Variant.SWEEP_MSS_IFVG,
            Variant.SWEEP_MSS_IFVG_HTF,
        )

    @property
    def zone_event(self) -> GapEventKind | None:
        if self in (Variant.MSS_FVG, Variant.SWEEP_MSS_FVG):
            return GapEventKind.FVG_CONFIRMED
        if self in (Variant.MSS_IFVG, Variant.SWEEP_MSS_IFVG, Variant.SWEEP_MSS_IFVG_HTF):
            return GapEventKind.IFVG_CONFIRMED
        return None


class SetupState(StrEnum):
    IDLE = "idle"
    CONTEXT_IDENTIFIED = "context_identified"
    LIQUIDITY_EVENT = "liquidity_event"
    MSS_CONFIRMED = "mss_confirmed"
    ZONE_IDENTIFIED = "zone_identified"
    RETEST_PENDING = "retest_pending"
    PAPER_SIGNAL = "paper_signal"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    variant: Variant = Variant.SWEEP_MSS_IFVG
    scope: Scope = Scope.INTERNAL
    sweep_to_mss_bars: int = 12
    mss_to_zone_bars: int = 10
    zone_to_retest_bars: int = 10
    stop_buffer_ticks: int = 1
    minimum_score: int = 0
    minimum_higher_alignment: int = 2
    allowed_sessions: tuple[ResearchSession, ...] = ()
    allowed_regimes: tuple[Regime, ...] = ()
    minimum_volatility_bps: int | None = None
    maximum_volatility_bps: int | None = None
    require_clear_event_risk: bool = False
    risk: RiskConfig = DEFAULT_RISK_CONFIG

    def __post_init__(self) -> None:
        if min(self.sweep_to_mss_bars, self.mss_to_zone_bars, self.zone_to_retest_bars) < 1:
            raise ValueError("strategy stage deadlines must be positive")
        if self.stop_buffer_ticks < 0 or not 0 <= self.minimum_score <= 100:
            raise ValueError("invalid strategy stop buffer or score threshold")
        if not 1 <= self.minimum_higher_alignment <= len(CONTEXT_FRAMES):
            raise ValueError("invalid higher-timeframe alignment threshold")
        lower, upper = self.minimum_volatility_bps, self.maximum_volatility_bps
        if any(value is not None and value < 0 for value in (lower, upper)):
            raise ValueError("volatility thresholds must be nonnegative")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("minimum volatility cannot exceed maximum volatility")


DEFAULT_STRATEGY_CONFIG = StrategyConfig()


@dataclass(frozen=True, slots=True)
class QualityScore:
    higher_context: int
    liquidity: int
    mss: int
    imbalance: int
    risk: int
    session: int
    volatility_and_events: int

    def as_dict(self) -> dict[str, object]:
        components = {
            "higher_context": self.higher_context,
            "liquidity": self.liquidity,
            "mss": self.mss,
            "imbalance": self.imbalance,
            "risk": self.risk,
            "session": self.session,
            "volatility_and_events": self.volatility_and_events,
        }
        return {
            "total": self.total,
            "components": components,
            "validated_comparable_samples": 0,
            "interpretation": "experimental quality score",
        }

    @property
    def total(self) -> int:
        return sum(
            (
                self.higher_context,
                self.liquidity,
                self.mss,
                self.imbalance,
                self.risk,
                self.session,
                self.volatility_and_events,
            )
        )


@dataclass(frozen=True, slots=True)
class SetupEvent:
    setup_id: str
    variant: Variant
    direction: Direction
    previous: SetupState
    state: SetupState
    source_opened_at: datetime
    confirmed_at: datetime
    context: ContextSnapshot
    sweep: LiquidityEvent | None
    mss: StructureBreak | None
    zone: GapZone | None
    stop_ticks: int | None
    plan: RiskPlan | None = None
    score: QualityScore | None = None
    matched: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    cautions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "event": "paper_setup_transition",
            "execution_mode": "paper_research",
            "strategy_version": STRATEGY_VERSION,
            "setup_id": self.setup_id,
            "variant": self.variant,
            "direction": self.direction,
            "previous": self.previous,
            "state": self.state,
            "source_opened_at": self.source_opened_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
            "context": self.context.as_dict(),
            "sweep": self.sweep.as_dict() if self.sweep else None,
            "mss": self.mss.as_dict() if self.mss else None,
            "zone": self.zone.as_dict() if self.zone else None,
            "stop_ticks": self.stop_ticks,
            "plan": self.plan.as_dict() if self.plan else None,
            "score": self.score.as_dict() if self.score else None,
            "matched": self.matched,
            "failed": self.failed,
            "cautions": self.cautions,
        }


@dataclass(slots=True)
class _Setup:
    setup_id: str
    direction: Direction
    state: SetupState
    stage_index: int
    sweep: LiquidityEvent | None = None
    mss: StructureBreak | None = None
    zone: GapZone | None = None
    stop_ticks: int | None = None


class SetupEngine:
    def __init__(self, config: StrategyConfig = DEFAULT_STRATEGY_CONFIG) -> None:
        self.config = config
        self._rule_id = sha256(canonical_json(asdict(config)).encode()).hexdigest()
        self._active: dict[Direction, _Setup] = {}
        self._index = 0
        self._last_close: datetime | None = None

    def push(
        self,
        bar: AggregateCandle,
        evidence: tuple[EvidenceEvent, ...],
        context: ContextSnapshot,
    ) -> tuple[SetupEvent, ...]:
        if self._last_close is not None and bar.opened_at < self._last_close:
            raise ValueError("setup decisions require chronological, non-overlapping candle closes")
        if any(event.confirmed_at != bar.available_at for event in evidence):
            raise ValueError("setup evidence must belong to the current analysis close")
        self._last_close = bar.available_at
        self._index += 1
        events = self._expire_and_invalidate(bar, evidence, context)
        if self.config.variant.needs_sweep:
            events.extend(self._observe_sweeps(bar, evidence, context))
        for item in evidence:
            if (
                isinstance(item, StructureBreak)
                and item.invalidation_pivot is not None
                and item.pivot.scope is self.config.scope
            ):
                events.extend(self._observe_mss(bar, item, context))

        zone_event = self.config.variant.zone_event
        if zone_event is not None:
            for item in evidence:
                if isinstance(item, GapEvent) and item.kind is zone_event:
                    setup = self._active.get(item.zone.direction)
                    if setup is not None and setup.state is SetupState.MSS_CONFIRMED:
                        setup.zone = item.zone
                        events.append(self._move(setup, SetupState.ZONE_IDENTIFIED, bar, context))
                        events.append(self._move(setup, SetupState.RETEST_PENDING, bar, context))

        for setup in tuple(self._active.values()):
            if (
                setup.state is SetupState.RETEST_PENDING
                and setup.zone is not None
                and _reaction(setup.zone, bar)
            ):
                events.append(self._qualify(setup, bar, context))
        return tuple(events)

    def _expire_and_invalidate(
        self, bar: AggregateCandle, evidence: tuple[EvidenceEvent, ...], context: ContextSnapshot
    ) -> list[SetupEvent]:
        events = []
        for direction, setup in tuple(self._active.items()):
            elapsed = self._index - setup.stage_index
            deadline = {
                SetupState.LIQUIDITY_EVENT: self.config.sweep_to_mss_bars,
                SetupState.MSS_CONFIRMED: self.config.mss_to_zone_bars,
                SetupState.RETEST_PENDING: self.config.zone_to_retest_bars,
            }[setup.state]
            if elapsed > deadline:
                events.append(
                    self._move(setup, SetupState.EXPIRED, bar, context, failed=("deadline",))
                )
                del self._active[direction]
                continue
            reason = _invalidation_reason(setup, bar, evidence)
            if reason is not None:
                events.append(
                    self._move(setup, SetupState.INVALIDATED, bar, context, failed=(reason,))
                )
                del self._active[direction]
        return events

    def _observe_sweeps(
        self, bar: AggregateCandle, evidence: tuple[EvidenceEvent, ...], context: ContextSnapshot
    ) -> Iterator[SetupEvent]:
        candidates: dict[Direction, LiquidityEvent] = {}
        for item in evidence:
            if not isinstance(item, LiquidityEvent) or item.kind is not LiquidityEventKind.SWEPT:
                continue
            sweep = item.sweep
            if sweep is None or sweep.direction in self._active:
                continue
            previous = candidates.get(sweep.direction)
            if previous is None or _sweep_rank(item) > _sweep_rank(previous):
                candidates[sweep.direction] = item
        for direction, item in candidates.items():
            assert item.sweep is not None
            setup = self._new(bar, direction, item.sweep.sweep_id)
            setup.sweep = item
            yield self._move(setup, SetupState.CONTEXT_IDENTIFIED, bar, context)
            yield self._move(setup, SetupState.LIQUIDITY_EVENT, bar, context)

    def _observe_mss(
        self, bar: AggregateCandle, item: StructureBreak, context: ContextSnapshot
    ) -> Iterator[SetupEvent]:
        setup = self._active.get(item.direction)
        if self.config.variant.needs_sweep:
            if (
                setup is None
                or setup.state is not SetupState.LIQUIDITY_EVENT
                or setup.sweep is None
                or setup.sweep.confirmed_at >= item.confirmed_at
            ):
                return
        else:
            if setup is not None:
                return
            setup = self._new(bar, item.direction, item.break_opened_at.isoformat())
            yield self._move(setup, SetupState.CONTEXT_IDENTIFIED, bar, context)

        setup.mss = item
        assert item.invalidation_pivot is not None
        stop = item.invalidation_pivot.price_ticks
        bullish = item.direction is Direction.BULLISH
        if setup.sweep is not None and setup.sweep.sweep is not None:
            extreme = setup.sweep.sweep.extreme_ticks
            stop = min(stop, extreme) if bullish else max(stop, extreme)
        setup.stop_ticks = (
            stop - self.config.stop_buffer_ticks
            if bullish
            else stop + self.config.stop_buffer_ticks
        )
        yield self._move(setup, SetupState.MSS_CONFIRMED, bar, context)
        if self.config.variant.zone_event is None:
            yield self._qualify(setup, bar, context)

    def _new(self, bar: AggregateCandle, direction: Direction, trigger: str) -> _Setup:
        identity = canonical_json(
            {
                "contract": bar.contract.code,
                "timeframe": bar.timeframe,
                "variant": self.config.variant,
                "rule_id": self._rule_id,
                "scope": self.config.scope,
                "direction": direction,
                "trigger": trigger,
                "strategy_version": STRATEGY_VERSION,
            }
        )
        setup = _Setup(
            sha256(identity.encode()).hexdigest(), direction, SetupState.IDLE, self._index
        )
        self._active[direction] = setup
        return setup

    def _qualify(self, setup: _Setup, bar: AggregateCandle, context: ContextSnapshot) -> SetupEvent:
        assert setup.stop_ticks is not None
        plan = size_paper_setup(
            setup.direction,
            bar.close_ticks,
            setup.stop_ticks,
            context.structural_prices,
            self.config.risk,
        )
        score = _score(setup, context, plan)
        checks = {
            "structural_sizing": plan.rejection is None,
            "score_threshold": score.total >= self.config.minimum_score,
        }
        if self.config.variant is Variant.SWEEP_MSS_IFVG_HTF:
            checks["higher_timeframe_alignment"] = (
                context.aligned_frames(setup.direction) >= self.config.minimum_higher_alignment
            )
        if self.config.allowed_sessions:
            checks["session_filter"] = context.session in self.config.allowed_sessions
        if self.config.allowed_regimes:
            checks["regime_filter"] = context.regime in self.config.allowed_regimes
        lower, upper = self.config.minimum_volatility_bps, self.config.maximum_volatility_bps
        if lower is not None or upper is not None:
            ratio = context.volatility_ratio_bps
            checks["volatility_filter"] = (
                ratio is not None
                and (lower is None or lower <= ratio)
                and (upper is None or ratio <= upper)
            )
        if self.config.require_clear_event_risk:
            checks["event_filter"] = context.economic.risk is EventRisk.CLEAR

        matched = ["qualifying_mss", *(name for name, passed in checks.items() if passed)]
        if setup.sweep is not None:
            matched.append("confirmed_sweep")
        if setup.zone is not None:
            matched.append("zone_retest_reaction")
        failed = tuple(name for name, passed in checks.items() if not passed)
        cautions = _cautions(context, plan)
        state = SetupState.INVALIDATED if failed else SetupState.PAPER_SIGNAL
        event = self._move(
            setup,
            state,
            bar,
            context,
            plan=plan,
            score=score,
            matched=tuple(matched),
            failed=failed,
            cautions=cautions,
        )
        del self._active[setup.direction]
        return event

    def _move(
        self,
        setup: _Setup,
        state: SetupState,
        bar: AggregateCandle,
        context: ContextSnapshot,
        *,
        plan: RiskPlan | None = None,
        score: QualityScore | None = None,
        matched: tuple[str, ...] = (),
        failed: tuple[str, ...] = (),
        cautions: tuple[str, ...] = (),
    ) -> SetupEvent:
        event = SetupEvent(
            setup.setup_id,
            self.config.variant,
            setup.direction,
            setup.state,
            state,
            bar.opened_at,
            bar.available_at,
            context,
            setup.sweep,
            setup.mss,
            setup.zone,
            setup.stop_ticks,
            plan,
            score,
            matched,
            failed,
            cautions,
        )
        setup.state = state
        setup.stage_index = self._index
        return event


class StrategyReplay:
    """Share market evidence and setup state between replay and incremental callers."""

    def __init__(
        self,
        calendar: SessionCalendar,
        contract: MgcContract,
        timeframe: Timeframe,
        structure: StructureConfig,
        strategy: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
        evidence: EvidenceConfig = DEFAULT_EVIDENCE_CONFIG,
        context: ContextConfig = DEFAULT_CONTEXT_CONFIG,
        economic: EconomicCalendar | None = None,
    ) -> None:
        self._evidence = EvidenceEngine(
            calendar,
            contract,
            timeframe,
            structure,
            evidence,
            extra_timeframes=CONTEXT_FRAMES,
        )
        self._context = MarketContext(contract, structure, context, economic)
        self._setups = SetupEngine(strategy)

    def push(self, candle: Candle) -> tuple[SetupEvent, ...]:
        step = self._evidence.step(candle)
        if step is None:
            return ()
        self._context.observe_higher(step.bars)
        if step.analysis is None:
            return ()
        context = self._context.observe_primary(step.analysis, step.events)
        return self._setups.push(step.analysis, step.events, context)

    def consume(self, candles: Iterable[Candle]) -> Iterator[SetupEvent]:
        for candle in candles:
            yield from self.push(candle)


def _invalidation_reason(
    setup: _Setup, bar: AggregateCandle, evidence: tuple[EvidenceEvent, ...]
) -> str | None:
    if setup.stop_ticks is not None:
        invalid = (
            bar.close_ticks <= setup.stop_ticks
            if setup.direction is Direction.BULLISH
            else bar.close_ticks >= setup.stop_ticks
        )
        if invalid:
            return "structural_invalidation"
    if setup.sweep is not None and setup.sweep.sweep is not None:
        sweep_id = setup.sweep.sweep.sweep_id
        for event in evidence:
            if (
                isinstance(event, LiquidityEvent)
                and event.sweep is not None
                and event.sweep.sweep_id == sweep_id
                and event.kind is LiquidityEventKind.SWEEP_INVALIDATED
            ):
                return "sweep_invalidation"
    if setup.zone is not None:
        for item in evidence:
            if (
                isinstance(item, GapEvent)
                and item.zone.zone_id == setup.zone.zone_id
                and item.kind
                in (
                    GapEventKind.FVG_INVALIDATED,
                    GapEventKind.IFVG_INVALIDATED,
                    GapEventKind.EXPIRED,
                )
            ):
                return "zone_unavailable"
    return None


def _reaction(zone: GapZone, bar: AggregateCandle) -> bool:
    if zone.confirmed_at >= bar.available_at:
        return False
    touches = bar.low_ticks <= zone.upper_ticks and bar.high_ticks >= zone.lower_ticks
    recovers = (
        bar.close_ticks > zone.upper_ticks
        if zone.direction is Direction.BULLISH
        else bar.close_ticks < zone.lower_ticks
    )
    return touches and recovers


def _sweep_rank(event: LiquidityEvent) -> tuple[int, str]:
    assert event.sweep is not None
    return event.sweep.penetration_ticks, event.level.level_id


def _score(setup: _Setup, context: ContextSnapshot, plan: RiskPlan) -> QualityScore:
    sweep = setup.sweep.sweep if setup.sweep else None
    liquidity = 10 + min(10, sweep.reclaim_ticks * 10 // sweep.penetration_ticks) if sweep else 0
    mss = 10 + min(10, setup.mss.displacement.body_range_bps // 1_000) if setup.mss else 0
    zone = 10 + min(5, setup.zone.width_ticks) if setup.zone else 0
    risk = 0 if plan.rejection else (5 if plan.intervening_level_ticks is not None else 10)
    session = {
        ResearchSession.OTHER: 0,
        ResearchSession.ASIA: 5,
        ResearchSession.LONDON: 10,
        ResearchSession.NEW_YORK: 10,
        ResearchSession.OVERLAP: 10,
    }[context.session]
    volatility = (
        5
        if context.volatility_ratio_bps is not None
        and context.regime not in (Regime.EXPANSION, Regime.CONTRACTION, Regime.THIN)
        else 0
    )
    events = 5 if context.economic.risk is EventRisk.CLEAR else 0
    return QualityScore(
        context.aligned_frames(setup.direction) * 5,
        liquidity,
        mss,
        zone,
        risk,
        session,
        volatility + events,
    )


def _cautions(context: ContextSnapshot, plan: RiskPlan) -> tuple[str, ...]:
    cautions = []
    if context.economic.risk is not EventRisk.CLEAR:
        cautions.append(f"economic_event_risk_{context.economic.risk}")
    if any(frame.available_at is None for frame in context.higher):
        cautions.append("higher_timeframe_history_incomplete")
    if plan.intervening_level_ticks is not None:
        cautions.append("intervening_confirmed_structure")
    return tuple(cautions)
