"""Development-fitted random admission controls for an independent paper holdout."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from aurum.exchange_calendar import SessionCalendar
from aurum.execution import (
    EXECUTION_VERSION,
    TRADE_PRINT_ORDERING,
    ExecutionConfig,
    JournalSignal,
    PaperExecution,
    TradePrintLookup,
)
from aurum.instruments import MgcContract
from aurum.market_data import canonical_json
from aurum.paper_replay import PaperReplayPath, replay_scheduled_paths
from aurum.paper_risk import RiskConfig, TargetMode
from aurum.performance import PERFORMANCE_VERSION
from aurum.statistics import bootstrap_expectancy, quantile
from aurum.storage import DatasetStore
from aurum.strategy import SetupState
from aurum.timestamps import MINUTE

CONTROL_VERSION = 1
_COMMON_RUN_FIELDS = (
    "dataset_id",
    "timeframe",
    "aggregation_version",
    "structure_version",
    "evidence_version",
    "context_version",
    "strategy_version",
    "execution_version",
    "performance_version",
    "backtest_version",
    "config",
    "evidence_config",
    "context_config",
    "economic_calendar",
    "execution_config",
    "intrabar_ordering",
)


class ControlStrata(StrEnum):
    SESSION_DIRECTION = "session_direction"
    SESSION_REGIME_DIRECTION = "session_regime_direction"


@dataclass(frozen=True, slots=True)
class ControlConfig:
    seed: int = 42
    replications: int = 30
    strata: ControlStrata = ControlStrata.SESSION_DIRECTION
    bootstrap_draws: int = 1000
    block_length: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.replications <= 100:
            raise ValueError("matched control requires 1 to 100 replications")
        if not 2 <= self.bootstrap_draws <= 10_000 or not 1 <= self.block_length <= 1000:
            raise ValueError("invalid matched-control bootstrap settings")


type Stratum = tuple[str, ...]


def _stratum(signal: JournalSignal, rule: ControlStrata) -> Stratum:
    if rule is ControlStrata.SESSION_REGIME_DIRECTION:
        return signal.context.session, signal.context.regime, signal.direction
    return signal.context.session, signal.direction


@dataclass(frozen=True, slots=True)
class _Admission:
    reference: int
    opportunities: int

    @property
    def numerator(self) -> int:
        return min(self.reference, self.opportunities)

    def admits(self, signal: JournalSignal, seed: int, replication: int) -> bool:
        if self.numerator == 0:
            return False
        key = canonical_json(
            (CONTROL_VERSION, seed, replication, signal.setup_id, signal.confirmed_at.isoformat())
        ).encode()
        draw = int.from_bytes(sha256(key).digest()[:8], byteorder="big")
        return draw * self.opportunities < self.numerator * (1 << 64)

    def as_dict(self, stratum: Stratum) -> dict[str, object]:
        return {
            "stratum": stratum,
            "development_reference_signals": self.reference,
            "development_opportunities": self.opportunities,
            "admission_rate": self.numerator / self.opportunities if self.opportunities else 0,
            "reference_rate_unattainable": self.reference > self.opportunities,
        }


def _qualified(
    store: DatasetStore,
    run_id: str,
    calibration_end: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
    rule: ControlStrata,
) -> tuple[Counter[Stratum], list[JournalSignal]]:
    fit: Counter[Stratum] = Counter()
    holdout = []
    for item in store.structure_events(run_id):
        if item["event"] != "paper_setup_transition" or item["state"] != SetupState.PAPER_SIGNAL:
            continue
        signal = JournalSignal.from_setup_payload(item)
        if signal.confirmed_at < calibration_end:
            fit[_stratum(signal, rule)] += 1
        elif holdout_start <= signal.confirmed_at < holdout_end:
            holdout.append(signal)
    return fit, holdout


def _paths(
    store: DatasetStore,
    dataset_id: str,
    calendar: SessionCalendar,
    contract: MgcContract,
    risk: RiskConfig,
    execution: ExecutionConfig,
    reference: list[JournalSignal],
    opportunities: list[JournalSignal],
    fit: dict[Stratum, _Admission],
    config: ControlConfig,
    end: datetime,
    lookup: TradePrintLookup | None,
) -> list[PaperReplayPath]:
    paths = [
        PaperReplayPath(
            PaperExecution(calendar, contract, risk, execution, trade_prints=lookup),
            index < 2,
        )
        for index in range(config.replications + 1)
    ]
    scheduled: dict[datetime, dict[int, list[JournalSignal]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for signal in reference:
        scheduled[signal.confirmed_at][0].append(signal)
    for signal in opportunities:
        rate = fit.get(_stratum(signal, config.strata))
        if rate is None or rate.numerator == 0:
            continue
        for replicate in range(config.replications):
            if rate.admits(signal, config.seed, replicate):
                scheduled[signal.confirmed_at][replicate + 1].append(signal)

    replay_scheduled_paths(store, dataset_id, paths, scheduled, end)
    return paths


def _control_distribution(summaries: list[dict[str, object]], key: str) -> dict[str, object] | None:
    samples = sorted(
        value for summary in summaries if isinstance(value := summary[key], int | float)
    )
    if not samples:
        return None
    return {
        "replications": len(samples),
        **{f"p{q:02d}": quantile(samples, q / 100) for q in (5, 25, 50, 75, 95)},
    }


def _assert_compatible(reference: dict[str, Any], control: dict[str, Any]) -> None:
    same_manifest = all(reference.get(key) == control.get(key) for key in _COMMON_RUN_FIELDS)
    first, second = reference["strategy_config"], control["strategy_config"]
    if not same_manifest or {key: value for key, value in first.items() if key != "variant"} != {
        key: value for key, value in second.items() if key != "variant"
    }:
        raise ValueError(
            "control and reference must share data, rules, risk and execution settings"
        )
    if first["variant"] == second["variant"]:
        raise ValueError("the control must use a distinct opportunity variant")
    if (
        reference["execution_version"] != EXECUTION_VERSION
        or reference["performance_version"] != PERFORMANCE_VERSION
    ):
        raise ValueError("rebuild both backtests with the current paper execution rules")


def save_matched_control(
    store: DatasetStore,
    validation_id: str,
    opportunity_run_id: str,
    config: ControlConfig | None = None,
) -> str:
    config = config if config is not None else ControlConfig()
    validation = store.describe_analysis(validation_id)
    result = validation["result"]
    if validation["kind"] not in (
        "chronological_validation",
        "flat_holdout_evaluation",
    ) or not isinstance(result, dict):
        raise ValueError("matched control requires a chronological validation report")
    holdout = result.get("holdout")
    if not isinstance(holdout, dict) or holdout.get("used_for_selection") is not False:
        raise ValueError("matched control requires a selected independent holdout")
    reference_run_id = str(holdout["run_id"])
    if result.get("selected_run_id") != reference_run_id:
        raise ValueError("validation selection and holdout identities differ")
    reference = store.describe_backtest(reference_run_id)
    control = store.describe_backtest(opportunity_run_id)
    _assert_compatible(reference, control)
    dataset_id = str(reference["dataset_id"])
    if result["dataset"]["dataset_id"] != dataset_id:
        raise ValueError("validation and backtest dataset identities differ")
    dataset = store.describe(dataset_id)
    calibration = datetime.fromisoformat(result["validation_start"])
    begin = datetime.fromisoformat(result["holdout_start"]) + MINUTE * result["embargo_minutes"]
    end = datetime.fromisoformat(dataset["last_close"])
    if result["last_close"] != dataset["last_close"]:
        raise ValueError("validation and dataset close bounds differ")
    if begin >= end:
        raise ValueError("the embargo consumes the entire holdout")
    reference_fit, reference_signals = _qualified(
        store, reference_run_id, calibration, begin, end, config.strata
    )
    control_fit, control_signals = _qualified(
        store, opportunity_run_id, calibration, begin, end, config.strata
    )
    fit = {
        group: _Admission(reference_fit[group], control_fit[group])
        for group in sorted(reference_fit.keys() | control_fit.keys())
    }
    provenance = dataset["provenance"]
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    strategy = reference["strategy_config"]
    opportunity_strategy = control["strategy_config"]
    execution_raw = reference["execution_config"]
    assert isinstance(strategy, dict) and isinstance(opportunity_strategy, dict)
    assert isinstance(execution_raw, dict)
    raw_risk = strategy["risk"]
    risk = RiskConfig(**{**raw_risk, "target_mode": TargetMode(raw_risk["target_mode"])})
    execution = ExecutionConfig(**execution_raw)
    ordering = reference.get("intrabar_ordering")
    if ordering not in (None, TRADE_PRINT_ORDERING):
        raise ValueError("unsupported intrabar ordering in saved backtest")
    lookup = store.trade_print_lookup(dataset_id) if ordering is not None else None
    paths = _paths(
        store,
        dataset_id,
        calendar,
        MgcContract(str(provenance["contract"])),
        risk,
        execution,
        reference_signals,
        control_signals,
        fit,
        config,
        end,
        lookup,
    )
    reference_path, *control_paths = paths
    reference_summary = reference_path.summary(execution.initial_equity_cents)
    controls = [path.summary(execution.initial_equity_cents) for path in control_paths]
    interval = bootstrap_expectancy(
        [trade.net_cents for trade in reference_path.trades],
        config.bootstrap_draws,
        config.block_length,
        config.seed,
    )
    definition = {
        "matched_control_version": CONTROL_VERSION,
        "validation_id": validation_id,
        "reference_run_id": reference_run_id,
        "opportunity_run_id": opportunity_run_id,
        "dataset_id": dataset_id,
        "reference_variant": strategy["variant"],
        "opportunity_variant": opportunity_strategy["variant"],
        "config": asdict(config),
        "development_end_exclusive": calibration.isoformat(),
        "holdout_start": begin.isoformat(),
        "holdout_end": end.isoformat(),
        "risk_config": raw_risk,
        "execution_config": execution_raw,
        **({"intrabar_ordering": ordering} if ordering is not None else {}),
    }
    return store.save_analysis(
        "matched_random_control",
        definition,
        {
            "execution_mode": "paper_research",
            "data_kind": provenance["data_kind"],
            "dataset": result["dataset"],
            "reference_strategy_version": reference["strategy_version"],
            "method": "development_fitted_bernoulli_admission_of_structural_opportunities",
            "holdout_used_for_calibration": False,
            "start_position": "flat_in_all_arms",
            "interpretation": "conditional_descriptive_comparison_on_shared_holdout",
            "fit": [item.as_dict(group) for group, item in fit.items()],
            "holdout_reference_opportunities": len(reference_signals),
            "holdout_control_opportunities": len(control_signals),
            "holdout_controls_without_positive_admission": sum(
                _stratum(signal, config.strata) not in fit
                or not fit[_stratum(signal, config.strata)].numerator
                for signal in control_signals
            ),
            "reference": reference_summary,
            "reference_bootstrap": interval.as_dict() if interval else None,
            "controls": controls,
            "control_trade_count_distribution": _control_distribution(controls, "trades"),
            "control_net_distribution_cents": _control_distribution(controls, "net_profit_cents"),
            "control_expectancy_distribution_cents": _control_distribution(
                controls, "expectancy_cents"
            ),
            "audit": {
                "reference": {"signals": reference_path.selected, "events": reference_path.events},
                "control_replication_0": {
                    "signals": control_paths[0].selected,
                    "events": control_paths[0].events,
                },
            },
        },
    )
