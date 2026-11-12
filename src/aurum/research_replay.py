"""Shared, versioned construction of persisted research replay engines."""

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Self

from aurum.backtesting import BACKTEST_VERSION, BacktestReplay
from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.economic_events import EconomicCalendar
from aurum.evidence import EVIDENCE_VERSION, EvidenceConfig, EvidenceEngine
from aurum.exchange_calendar import SessionCalendar
from aurum.execution import (
    EXECUTION_VERSION,
    TRADE_PRINT_ORDERING,
    ExecutionConfig,
    TradePrintLookup,
)
from aurum.imbalances import ImbalanceConfig
from aurum.instruments import MgcContract
from aurum.liquidity import LiquidityConfig
from aurum.liquidity_map import LiquidityMapConfig
from aurum.market_context import CONTEXT_VERSION, ContextConfig, Regime, ResearchSession
from aurum.market_data import Candle, canonical_json
from aurum.market_structure import (
    STRUCTURE_VERSION,
    BreakMode,
    BreakRule,
    PivotRule,
    Scope,
    StructureConfig,
    StructureEngine,
)
from aurum.paper_risk import RiskConfig, TargetMode
from aurum.performance import PERFORMANCE_VERSION
from aurum.sessions import SessionHours
from aurum.strategy import STRATEGY_VERSION, StrategyConfig, StrategyReplay, Variant


class RecordedResearchEvent(Protocol):
    def as_dict(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ResearchReplay:
    dataset_id: str
    timeframe: Timeframe
    structure: StructureConfig
    evidence: EvidenceConfig | None = None
    context: ContextConfig | None = None
    strategy: StrategyConfig | None = None
    economic: EconomicCalendar | None = None
    execution: ExecutionConfig | None = None
    resolve_with_trade_prints: bool = False

    def __post_init__(self) -> None:
        if self.strategy is None:
            if self.context is not None or self.economic is not None or self.execution is not None:
                raise ValueError("context and execution require a strategy replay")
        elif self.evidence is None or self.context is None:
            raise ValueError("strategy replay requires evidence and context configurations")
        if self.resolve_with_trade_prints and self.execution is None:
            raise ValueError("trade print ordering requires a paper backtest")

    def manifest(self) -> dict[str, object]:
        result: dict[str, object] = {
            "dataset_id": self.dataset_id,
            "timeframe": self.timeframe,
            "aggregation_version": AGGREGATION_VERSION,
            "structure_version": STRUCTURE_VERSION,
            "config": asdict(self.structure),
        }
        if self.evidence is not None:
            result.update(evidence_version=EVIDENCE_VERSION, evidence_config=asdict(self.evidence))
        if self.strategy is not None:
            assert self.context is not None
            result.update(
                context_version=CONTEXT_VERSION,
                context_config=asdict(self.context),
                strategy_version=STRATEGY_VERSION,
                strategy_config=asdict(self.strategy),
                economic_calendar=self.economic.as_dict() if self.economic else None,
                execution_mode="paper_research",
            )
        if self.execution is not None:
            result.update(
                backtest_version=BACKTEST_VERSION,
                execution_version=EXECUTION_VERSION,
                performance_version=PERFORMANCE_VERSION,
                execution_config=asdict(self.execution),
            )
        if self.resolve_with_trade_prints:
            result["intrabar_ordering"] = TRADE_PRINT_ORDERING
        return result

    def events(
        self,
        calendar: SessionCalendar,
        contract: MgcContract,
        minutes: Iterator[Candle],
        *,
        trade_prints: TradePrintLookup | None = None,
    ) -> Iterator[RecordedResearchEvent]:
        if self.resolve_with_trade_prints != (trade_prints is not None):
            raise ValueError("trade print lookup differs from the replay definition")
        if self.strategy is not None:
            assert self.evidence is not None and self.context is not None
            if self.execution is not None:
                yield from BacktestReplay(
                    calendar,
                    contract,
                    self.timeframe,
                    self.structure,
                    self.strategy,
                    self.execution,
                    self.evidence,
                    self.context,
                    self.economic,
                    trade_prints=trade_prints,
                ).consume(minutes)
            else:
                yield from StrategyReplay(
                    calendar,
                    contract,
                    self.timeframe,
                    self.structure,
                    self.strategy,
                    self.evidence,
                    self.context,
                    self.economic,
                ).consume(minutes)
        elif self.evidence is not None:
            yield from EvidenceEngine(
                calendar, contract, self.timeframe, self.structure, self.evidence
            ).consume(minutes)
        else:
            aggregator = CandleAggregator(calendar, contract, (self.timeframe,))
            engine = StructureEngine(contract, self.timeframe, self.structure)
            for bar in aggregator.consume(minutes):
                yield from engine.push(bar)

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> Self:
        versions = {
            "aggregation_version": AGGREGATION_VERSION,
            "structure_version": STRUCTURE_VERSION,
            "evidence_version": EVIDENCE_VERSION,
            "context_version": CONTEXT_VERSION,
            "strategy_version": STRATEGY_VERSION,
            "backtest_version": BACKTEST_VERSION,
            "execution_version": EXECUTION_VERSION,
            "performance_version": PERFORMANCE_VERSION,
        }
        for name, supported in versions.items():
            if name in manifest and (
                type(manifest[name]) is not int or manifest[name] != supported
            ):
                raise ValueError(f"research replay requires the original {name}")
        try:
            raw_structure = manifest["config"]
            breaks = raw_structure["breaks"]
            structure = StructureConfig(
                internal=PivotRule(**raw_structure["internal"]),
                external=PivotRule(**raw_structure["external"]),
                breaks=BreakRule(**{**breaks, "mode": BreakMode(breaks["mode"])}),
            )
            evidence = None
            if "evidence_config" in manifest:
                raw = manifest["evidence_config"]
                mapping = raw["mapping"]
                evidence = EvidenceConfig(
                    liquidity=LiquidityConfig(**raw["liquidity"]),
                    mapping=LiquidityMapConfig(
                        **{
                            **mapping,
                            "swing_scope": Scope(mapping["swing_scope"]),
                            "asia_hours": SessionHours(**mapping["asia_hours"]),
                            "london_hours": SessionHours(**mapping["london_hours"]),
                        }
                    ),
                    imbalances=ImbalanceConfig(**raw["imbalances"]),
                )
            context = strategy = economic = execution = None
            if "strategy_config" in manifest:
                raw = manifest["context_config"]
                context = ContextConfig(
                    **{
                        **raw,
                        **{
                            name: SessionHours(**raw[name])
                            for name in ("asia", "london", "new_york")
                        },
                    }
                )
                raw = manifest["strategy_config"]
                risk = raw["risk"]
                strategy = StrategyConfig(
                    **{
                        **raw,
                        "variant": Variant(raw["variant"]),
                        "scope": Scope(raw["scope"]),
                        "allowed_sessions": tuple(
                            ResearchSession(value) for value in raw["allowed_sessions"]
                        ),
                        "allowed_regimes": tuple(Regime(value) for value in raw["allowed_regimes"]),
                        "risk": RiskConfig(
                            **{**risk, "target_mode": TargetMode(risk["target_mode"])}
                        ),
                    }
                )
                if manifest["economic_calendar"] is not None:
                    economic = EconomicCalendar.from_dict(manifest["economic_calendar"])
            if "execution_config" in manifest:
                execution = ExecutionConfig(**manifest["execution_config"])
            result = cls(
                dataset_id=manifest["dataset_id"],
                timeframe=Timeframe(manifest["timeframe"]),
                structure=structure,
                evidence=evidence,
                context=context,
                strategy=strategy,
                economic=economic,
                execution=execution,
                resolve_with_trade_prints="intrabar_ordering" in manifest,
            )
            if canonical_json(result.manifest()) != canonical_json(dict(manifest)):
                raise ValueError("research definition is incomplete or has unsupported fields")
            return result
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"invalid saved research definition: {exc}") from None
