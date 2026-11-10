from dataclasses import asdict
from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aurum.account_reports import simulate_account_document
from aurum.account_simulation import LUCIDFLEX_25K, AccountPhase, AccountRules
from aurum.control import ControlConfig, ControlStrata, save_matched_control
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.forward_reports import ForwardPeriod, save_forward_report
from aurum.intrabar_reports import save_intrabar_comparison
from aurum.market_data import canonical_json
from aurum.monte_carlo import (
    REFERENCE_WIN_RATES_BPS,
    MonteCarloConfig,
    SamplingMode,
    TrailingLossScenario,
    WinRateScenario,
)
from aurum.quotes import DEFAULT_QUOTE_MAX_AGE_MS, MAX_QUOTE_AGE_MS
from aurum.reconciliation import save_reconciliation
from aurum.research_reports import simulate_report, validate_runs
from aurum.sensitivity_reports import SensitivityFactor, save_parameter_sensitivity
from aurum.spread_reports import save_spread_report
from aurum.statistics import ValidationConfig
from aurum.storage import DatasetStore
from aurum.timestamps import minute_timestamp

_RunId = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_AdditionalCost = Annotated[int, Field(ge=0, le=100_000)]


class IntrabarComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_first_run_id: _RunId
    trade_print_run_id: _RunId
    example_limit: int = Field(default=20, ge=0, le=100)

    def run(self, store: DatasetStore) -> str:
        return save_intrabar_comparison(
            store,
            self.stop_first_run_id,
            self.trade_print_run_id,
            example_limit=self.example_limit,
        )


class SpreadObservationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: _RunId
    quote_series_id: _RunId
    maximum_age_ms: int = Field(default=DEFAULT_QUOTE_MAX_AGE_MS, ge=0, le=MAX_QUOTE_AGE_MS)
    example_limit: int = Field(default=10, ge=0, le=100)
    include_quote_cost_proxy: bool = False

    def run(self, store: DatasetStore) -> str:
        return save_spread_report(
            store,
            self.run_id,
            self.quote_series_id,
            maximum_age_ms=self.maximum_age_ms,
            example_limit=self.example_limit,
            include_quote_cost_proxy=self.include_quote_cost_proxy,
        )


class ReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_dataset_id: _RunId
    comparison_dataset_id: _RunId
    start: str | None = None
    end: str | None = None
    example_limit: int = Field(default=20, ge=0, le=100)

    def run(self, store: DatasetStore) -> str:
        if (self.start is None) != (self.end is None):
            raise ValueError("specify both comparison boundaries or neither")
        span = (
            TradingWindow(minute_timestamp(self.start), minute_timestamp(self.end))
            if self.start is not None and self.end is not None
            else None
        )
        return save_reconciliation(
            store,
            self.primary_dataset_id,
            self.comparison_dataset_id,
            span=span,
            example_limit=self.example_limit,
        )


class ValidationReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: tuple[_RunId, ...] = Field(min_length=1, max_length=16)
    validation_start: str
    holdout_start: str
    embargo_minutes: int = Field(default=0, ge=0, le=10_080)
    minimum_selection_trades: int = Field(default=20, ge=1, le=100_000)
    bootstrap_draws: int = Field(default=2_000, ge=2, le=10_000)
    block_length: int = Field(default=5, ge=1, le=1000)
    seed: int = 42
    walk_train_days: int = Field(default=90, ge=1, le=3650)
    walk_test_days: int = Field(default=30, ge=1, le=3650)
    extra_cost_per_contract_cents: tuple[_AdditionalCost, ...] = Field(
        default=(0, 50, 100, 200), min_length=1, max_length=20
    )

    def run(self, store: DatasetStore) -> str:
        config = ValidationConfig(
            validation_start=minute_timestamp(self.validation_start),
            holdout_start=minute_timestamp(self.holdout_start),
            embargo_minutes=self.embargo_minutes,
            minimum_selection_trades=self.minimum_selection_trades,
            bootstrap_draws=self.bootstrap_draws,
            block_length=self.block_length,
            seed=self.seed,
            walk_train_days=self.walk_train_days,
            walk_test_days=self.walk_test_days,
            extra_cost_per_contract_cents=self.extra_cost_per_contract_cents,
        )
        return validate_runs(store, self.run_ids, config)


class MatchedControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_id: _RunId
    opportunity_run_id: _RunId
    seed: int = 42
    replications: int = Field(default=30, ge=1, le=100)
    strata: ControlStrata = ControlStrata.SESSION_DIRECTION
    bootstrap_draws: int = Field(default=1000, ge=2, le=10_000)
    block_length: int = Field(default=5, ge=1, le=1000)

    def run(self, store: DatasetStore) -> str:
        config = ControlConfig(
            self.seed, self.replications, self.strata, self.bootstrap_draws, self.block_length
        )
        return save_matched_control(store, self.validation_id, self.opportunity_run_id, config)


class ParameterSensitivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_id: _RunId
    baseline_run_id: _RunId
    factor: SensitivityFactor
    run_ids: tuple[_RunId, ...] = Field(default=(), max_length=16)

    def run(self, store: DatasetStore) -> str:
        return save_parameter_sensitivity(
            store, self.validation_id, self.baseline_run_id, self.factor, run_ids=self.run_ids
        )


class MonteCarloReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: SamplingMode = SamplingMode.SCENARIO
    config: MonteCarloConfig = MonteCarloConfig()
    scenarios: tuple[WinRateScenario, ...] = Field(default=(), max_length=12)
    run_id: _RunId | None = None
    trailing: TrailingLossScenario | None = None

    @model_validator(mode="after")
    def check_work_limit(self) -> Self:
        if self.config.paths * self.config.trades * max(len(self.scenarios), 1) > 10_000_000:
            raise ValueError("local simulation limit is ten million modeled trade steps")
        return self

    def run(self, store: DatasetStore) -> str:
        return simulate_report(
            store, self.config, self.mode, self.scenarios, self.run_id, trailing=self.trailing
        )


class PaperAccountReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar: dict[str, object]
    ledger: dict[str, object]
    phase: AccountPhase = AccountPhase.EVALUATION
    rules: AccountRules = LUCIDFLEX_25K

    def run(self, store: DatasetStore) -> str:
        actions = self.ledger.get("actions")
        if not isinstance(actions, list) or len(actions) > 100_000:
            raise ValueError("paper ledger requires at most 100000 explicit actions")
        if self.rules.inactivity_days > 3650:
            raise ValueError("paper inactivity horizon cannot exceed 3650 days")
        return simulate_account_document(
            store,
            canonical_json(self.ledger).encode(),
            SessionCalendar.from_dict(self.calendar),
            self.phase,
            self.rules,
        )


class ForwardReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forward_id: _RunId
    period: ForwardPeriod = ForwardPeriod.DAILY
    trade_date: date
    baseline_validation_id: _RunId | None = None
    bootstrap_draws: int = Field(default=1000, ge=2, le=10000)
    block_length: int = Field(default=5, ge=1, le=1000)
    seed: int = 42

    def run(self, store: DatasetStore) -> str:
        return save_forward_report(
            store,
            self.forward_id,
            self.period,
            self.trade_date,
            baseline_validation_id=self.baseline_validation_id,
            bootstrap_draws=self.bootstrap_draws,
            block_length=self.block_length,
            seed=self.seed,
        )


def report_configuration() -> dict[str, object]:
    return {
        "matched_control": asdict(ControlConfig()),
        "control_strata": list(ControlStrata),
        "sensitivity_factors": list(SensitivityFactor),
        "spread_maximum_age_ms": DEFAULT_QUOTE_MAX_AGE_MS,
        "monte_carlo": asdict(MonteCarloConfig()),
        "sampling_modes": list(SamplingMode),
        "assumed_scenarios": [asdict(WinRateScenario(rate)) for rate in REFERENCE_WIN_RATES_BPS],
        "account_rules": asdict(LUCIDFLEX_25K),
        "account_phases": list(AccountPhase),
    }
