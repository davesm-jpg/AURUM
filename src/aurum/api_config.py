from dataclasses import replace
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aurum.candles import Timeframe
from aurum.economic_archive import normalize_archived_ics
from aurum.economic_events import EconomicCalendar
from aurum.economic_ics import MAX_ECONOMIC_CAPTURES
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EvidenceConfig
from aurum.exchange_calendar import TradingWindow
from aurum.execution import DEFAULT_EXECUTION_CONFIG, ExecutionConfig
from aurum.instruments import MGC
from aurum.market_context import DEFAULT_CONTEXT_CONFIG, ContextConfig, Regime, ResearchSession
from aurum.market_structure import (
    DEFAULT_STRUCTURE_CONFIG,
    BreakMode,
    PivotRule,
    Scope,
    StructureConfig,
)
from aurum.paper_risk import DEFAULT_RISK_CONFIG, TargetMode
from aurum.runtime_profiles import DeliveryProfile, ProviderProfile
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, StrategyConfig, Variant
from aurum.timestamps import minute_timestamp


class EconomicCapturePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_ids: tuple[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")], ...] = Field(
        min_length=1, max_length=MAX_ECONOMIC_CAPTURES
    )
    start: str
    end: str
    title_prefixes: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(
        min_length=1, max_length=32
    )

    def preview(self, store: DatasetStore) -> EconomicCalendar:
        return normalize_archived_ics(
            store,
            self.capture_ids,
            coverage=TradingWindow(minute_timestamp(self.start), minute_timestamp(self.end)),
            title_prefixes=self.title_prefixes,
        )


class BacktestProfile(BaseModel):
    """Domain configuration stored in an immutable paper backtest manifest."""

    model_config = ConfigDict(extra="forbid")

    config: StructureConfig = DEFAULT_STRUCTURE_CONFIG
    evidence_config: EvidenceConfig = DEFAULT_EVIDENCE_CONFIG
    context_config: ContextConfig = DEFAULT_CONTEXT_CONFIG
    strategy_config: StrategyConfig = DEFAULT_STRATEGY_CONFIG
    execution_config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG
    economic_calendar: dict[str, object] | None = None
    resolve_with_trade_prints: bool = False

    @model_validator(mode="after")
    def check_replay_bounds(self) -> Self:
        structure, context, evidence = self.config, self.context_config, self.evidence_config
        if any(
            value > 10_000
            for value in (
                structure.internal.left,
                structure.internal.right,
                structure.external.left,
                structure.external.right,
                structure.breaks.atr_period,
                context.short_atr_period,
                context.baseline_period,
                context.efficiency_period,
                evidence.mapping.swing_history,
                evidence.mapping.range_bars,
                evidence.imbalances.atr_period,
                evidence.imbalances.max_age_bars,
                evidence.liquidity.max_level_age_bars,
                evidence.liquidity.max_sweep_age_bars,
            )
        ):
            raise ValueError("research lookbacks cannot exceed 10000 bars")
        if (
            self.execution_config.entry_timeout_minutes > 1_440
            or context.event_buffer_minutes > 1_440
        ):
            raise ValueError("execution timeout and event buffer cannot exceed 1440 minutes")
        return self

    def economic_events(self) -> EconomicCalendar | None:
        if self.economic_calendar is None:
            return None
        return EconomicCalendar.from_dict(self.economic_calendar)


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeframe: Timeframe = Timeframe.FIVE_MINUTES
    profile: BacktestProfile | None = None
    variant: Variant = Variant.SWEEP_MSS_IFVG
    break_mode: BreakMode = BreakMode.CLOSE
    pivot_left: int = Field(default=3, ge=1, le=50)
    pivot_right: int = Field(default=3, ge=1, le=50)
    gross_risk_cents: int = Field(default=5_000, ge=1, le=1_000_000)
    commission_cents: int = Field(default=160, ge=0, le=100_000)
    slippage_ticks: int = Field(default=1, ge=0, le=1_000)
    latency_minutes: int = Field(default=0, ge=0, le=60)
    target_mode: TargetMode = TargetMode.FIXED

    @model_validator(mode="after")
    def prevent_mixed_profiles(self) -> Self:
        if self.profile is not None and self.model_fields_set - {
            "dataset_id",
            "timeframe",
            "profile",
        }:
            raise ValueError("choose either a research profile or flat backtest settings")
        return self

    def resolved(self) -> BacktestProfile:
        if self.profile is not None:
            return self.profile
        structure = replace(
            DEFAULT_STRUCTURE_CONFIG,
            internal=PivotRule(self.pivot_left, self.pivot_right),
            breaks=replace(DEFAULT_STRUCTURE_CONFIG.breaks, mode=self.break_mode),
        )
        risk = replace(
            DEFAULT_RISK_CONFIG,
            gross_allowance_cents=self.gross_risk_cents,
            commission_round_trip_cents=self.commission_cents,
            slippage_round_trip_ticks=self.slippage_ticks,
            target_mode=self.target_mode,
        )
        return BacktestProfile(
            config=structure,
            strategy_config=replace(DEFAULT_STRATEGY_CONFIG, variant=self.variant, risk=risk),
            execution_config=replace(
                DEFAULT_EXECUTION_CONFIG,
                latency_minutes=self.latency_minutes,
                entry_timeout_minutes=max(
                    DEFAULT_EXECUTION_CONFIG.entry_timeout_minutes, self.latency_minutes
                ),
            ),
        )


class ForwardStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitor_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    synthetic: bool = False
    execution_config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG


class ProviderProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ProviderProfile


class DeliveryProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: DeliveryProfile


def backtest_configuration() -> dict[str, object]:
    return {
        "execution_mode": "paper_research",
        "instrument": MGC.symbol,
        "timeframe": Timeframe.FIVE_MINUTES,
        "profile": BacktestProfile().model_dump(mode="json"),
        "choices": {
            "timeframes": list(Timeframe),
            "variants": list(Variant),
            "break_modes": list(BreakMode),
            "scopes": list(Scope),
            "target_modes": list(TargetMode),
            "sessions": list(ResearchSession),
            "regimes": list(Regime),
        },
    }
