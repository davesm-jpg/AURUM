from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from statistics import median, stdev

from aurum.monte_carlo import (
    MONTE_CARLO_VERSION,
    MonteCarloConfig,
    SamplingMode,
    TrailingLossScenario,
    WinRateScenario,
    simulate,
)
from aurum.statistics import (
    VALIDATION_VERSION,
    Candidate,
    Observation,
    ValidationConfig,
    cost_sensitivity,
    evaluate,
    outcomes_by_score,
    rolling_outcomes,
)
from aurum.storage import DatasetStore

_COMPARABLE_FIELDS = (
    "dataset_id",
    "timeframe",
    "aggregation_version",
    "structure_version",
    "evidence_version",
    "context_version",
    "strategy_version",
    "backtest_version",
    "execution_version",
    "performance_version",
    "economic_calendar",
    "execution_config",
    "intrabar_ordering",
)
BACKTEST_ANALYTICS_VERSION = 2
VALIDATION_SELECTION_VERSION = 1


@dataclass(frozen=True, slots=True)
class _TradeDiagnostics:
    observation: Observation
    trade_date: date
    holding_seconds: int
    mfe_ticks: int
    mae_ticks: int
    execution_cost_cents: int
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class _BacktestEvidence:
    candidate: Candidate
    signals_by_date: Counter[str]
    signals_by_session: Counter[str]
    trade_diagnostics: tuple[_TradeDiagnostics, ...]


def validate_runs(
    store: DatasetStore,
    run_ids: tuple[str, ...],
    config: ValidationConfig,
    *,
    selection_only: bool = False,
) -> str:
    if not run_ids or len(set(run_ids)) != len(run_ids):
        raise ValueError("provide distinct backtest run IDs")
    describe_run = store.backtest_manifest if selection_only else store.describe_backtest
    runs = {run_id: describe_run(run_id) for run_id in sorted(run_ids)}
    first = next(iter(runs.values()))
    if any(not _comparable(first, run) for run in runs.values()):
        raise ValueError("candidate backtests must share data, evidence and execution assumptions")
    dataset = store.describe(str(first["dataset_id"]))
    definition: dict[str, object] = {
        **asdict(config),
        "validation_start": config.validation_start.isoformat(),
        "holdout_start": config.holdout_start.isoformat(),
        "validation_version": VALIDATION_VERSION,
        "candidate_run_ids": sorted(run_ids),
        "dataset": _source(dataset["provenance"], str(dataset["dataset_id"])),
    }
    cutoff = config.holdout_start if selection_only else None
    report = evaluate(
        [_load_backtest_evidence(store, run_id, before=cutoff).candidate for run_id in runs],
        datetime.fromisoformat(dataset["first_open"]),
        datetime.fromisoformat(dataset["last_close"]),
        config,
        selection_only=selection_only,
    )
    report["data_kind"] = dataset["provenance"]["data_kind"]
    report["dataset"] = definition["dataset"]
    report["candidate_configs"] = {
        run_id: {
            field: run[field]
            for field in ("config", "evidence_config", "context_config", "strategy_config")
        }
        for run_id, run in runs.items()
    }
    report["execution_config"] = first["execution_config"]
    if selection_only:
        definition["validation_selection_version"] = VALIDATION_SELECTION_VERSION
        report["validation_selection_version"] = VALIDATION_SELECTION_VERSION
        report["sample_kind"] = "development_and_validation"
        return store.save_analysis("chronological_selection", definition, report)
    return store.save_analysis("chronological_validation", definition, report)


def simulate_report(
    store: DatasetStore,
    config: MonteCarloConfig,
    mode: SamplingMode,
    scenarios: tuple[WinRateScenario, ...] = (),
    run_id: str | None = None,
    *,
    trailing: TrailingLossScenario | None = None,
) -> str:
    if mode is SamplingMode.SCENARIO:
        if run_id is not None or not scenarios:
            raise ValueError("scenario simulations require assumptions without a backtest run")
        source: dict[str, object] = {"data_kind": "assumed", "observations": 0}
        results = [
            simulate(config, mode, scenario=scenario, trailing=trailing) for scenario in scenarios
        ]
    else:
        if run_id is None or scenarios:
            raise ValueError("resampling requires one backtest run and no assumed payouts")
        run = store.describe_backtest(run_id)
        dataset = store.describe(str(run["dataset_id"]))
        source = {
            **_source(dataset["provenance"], str(dataset["dataset_id"])),
            "backtest_run_id": run_id,
            "first_open": dataset["first_open"],
            "last_close": dataset["last_close"],
        }
        candidate = load_candidate(store, run_id)
        results = [
            simulate(
                config,
                mode,
                [item.net_cents for item in candidate.observations],
                trailing=trailing,
            )
        ]
    definition = {
        "monte_carlo_version": MONTE_CARLO_VERSION,
        "config": asdict(config),
        "mode": mode,
        "scenarios": [asdict(scenario) for scenario in scenarios],
        "source": source,
    }
    if trailing is not None:
        definition["trailing"] = trailing.as_dict()
    return store.save_analysis(
        "monte_carlo", definition, {"source": source, "simulations": results}
    )


def load_candidate(store: DatasetStore, run_id: str) -> Candidate:
    return _load_backtest_evidence(store, run_id).candidate


def backtest_analytics(store: DatasetStore, run_id: str, window: int) -> dict[str, object]:
    run = store.describe_backtest(run_id)
    dataset_id = str(run["dataset_id"])
    dataset = store.describe(dataset_id)
    evidence = _load_backtest_evidence(store, run_id, include_diagnostics=True)
    items = evidence.candidate.observations
    return {
        "backtest_analytics_version": BACKTEST_ANALYTICS_VERSION,
        "sample_kind": "unpartitioned_replay",
        "run_id": run_id,
        "dataset": _source(dataset["provenance"], dataset_id),
        "first_open": dataset["first_open"],
        "last_close": dataset["last_close"],
        "completed_trades": len(items),
        "scored_trades": sum(item.score is not None for item in items),
        "signal_count": evidence.signals_by_date.total(),
        "signals_by_utc_date": dict(sorted(evidence.signals_by_date.items())),
        "signals_by_session": dict(sorted(evidence.signals_by_session.items())),
        "rolling_window_trades": window,
        "rolling_outcomes": rolling_outcomes(items, window),
        "by_score_decile": outcomes_by_score(items),
        "cost_sensitivity_fixed_fills": cost_sensitivity(items, (0, 50, 100, 200)),
        "execution_diagnostics": _execution_diagnostics(evidence.trade_diagnostics),
    }


def _load_backtest_evidence(
    store: DatasetStore,
    run_id: str,
    *,
    include_diagnostics: bool = False,
    before: datetime | None = None,
) -> _BacktestEvidence:
    signals: dict[str, tuple[datetime, int | None]] = {}
    observations = []
    by_date: Counter[str] = Counter()
    by_session: Counter[str] = Counter()
    diagnostics = []
    items = (
        store.structure_events(run_id)
        if before is None
        else (
            payload
            for _, payload in store.timed_structure_payloads(
                run_id,
                None,
                before,
                end_exclusive=True,
                kinds=("paper_setup_transition", "paper_trade_completed", "paper_entry_canceled"),
            )
        )
    )
    for item in items:
        if item["event"] == "paper_setup_transition" and item["state"] == "paper_signal":
            score = item["score"]
            total = score["total"] if isinstance(score, dict) else None
            decision = datetime.fromisoformat(str(item["confirmed_at"]))
            signals[str(item["setup_id"])] = decision, total
            by_date[decision.date().isoformat()] += 1
            context = item["context"]
            by_session[str(context["session"]) if isinstance(context, dict) else "unknown"] += 1
        elif item["event"] == "paper_trade_completed":
            setup = str(item["setup_id"])
            if setup not in signals:
                raise ValueError("backtest trade has no earlier setup signal")
            decision, score_value = signals.pop(setup)
            observation = Observation(
                setup,
                decision,
                datetime.fromisoformat(str(item["entry_at"])),
                datetime.fromisoformat(str(item["recorded_at"])),
                _integer(item["net_cents"]),
                _integer(item["quantity"]),
                str(item["session"]),
                str(item["regime"]),
                str(item["direction"]),
                _integer(score_value) if score_value is not None else None,
            )
            observations.append(observation)
            if include_diagnostics:
                diagnostics.append(
                    _TradeDiagnostics(
                        observation,
                        date.fromisoformat(str(item["trade_date"])),
                        _integer(item["holding_seconds"]),
                        _integer(item["mfe_ticks"]),
                        _integer(item["mae_ticks"]),
                        sum(
                            _integer(item[key])
                            for key in ("commission_cents", "slippage_cents", "spread_cents")
                        ),
                        item["ambiguous_stop_target"] is True,
                    )
                )
        elif item["event"] == "paper_entry_canceled" and item["reason"] != "duplicate_setup":
            signals.pop(str(item["setup_id"]), None)
    return _BacktestEvidence(
        Candidate(run_id, tuple(observations)), by_date, by_session, tuple(diagnostics)
    )


def _execution_diagnostics(trades: tuple[_TradeDiagnostics, ...]) -> dict[str, object]:
    groups: dict[str, defaultdict[str, list[_TradeDiagnostics]]] = {
        name: defaultdict(list)
        for name in ("by_month", "by_weekday", "by_session", "by_regime", "by_direction")
    }
    for trade in trades:
        observation = trade.observation
        groups["by_month"][trade.trade_date.isoformat()[:7]].append(trade)
        groups["by_weekday"][str(trade.trade_date.isoweekday())].append(trade)
        groups["by_session"][observation.session].append(trade)
        groups["by_regime"][observation.regime].append(trade)
        groups["by_direction"][observation.direction].append(trade)

    monthly_nets = [
        sum(trade.observation.net_cents for trade in group) for group in groups["by_month"].values()
    ]
    return {
        "months_with_trades": len(monthly_nets),
        "profitable_months": sum(net > 0 for net in monthly_nets),
        "monthly_net_stddev_cents": stdev(monthly_nets) if len(monthly_nets) > 1 else None,
        **{
            name: {label: _diagnostic_summary(group) for label, group in sorted(partitions.items())}
            for name, partitions in groups.items()
        },
    }


def _diagnostic_summary(trades: list[_TradeDiagnostics]) -> dict[str, object]:
    net = [trade.observation.net_cents for trade in trades]
    holding = [trade.holding_seconds for trade in trades]
    count = len(trades)
    return {
        "trades": count,
        "average_holding_seconds": sum(holding) / count,
        "median_holding_seconds": median(holding),
        "average_mfe_ticks": sum(trade.mfe_ticks for trade in trades) / count,
        "average_mae_ticks": sum(trade.mae_ticks for trade in trades) / count,
        "max_mfe_ticks": max(trade.mfe_ticks for trade in trades),
        "max_mae_ticks": max(trade.mae_ticks for trade in trades),
        "net_trade_stddev_cents": stdev(net) if count > 1 else None,
        "execution_cost_cents": sum(trade.execution_cost_cents for trade in trades),
        "ambiguous_exits": sum(trade.ambiguous for trade in trades),
    }


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected integer backtest evidence")
    return value


def _source(provenance: dict[str, object], dataset_id: str) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        **{
            field: provenance[field]
            for field in ("data_kind", "contract", "provider", "source_sha256", "dataset_version")
        },
    }


def _comparable(first: dict[str, object], second: dict[str, object]) -> bool:
    return all(first.get(key) == second.get(key) for key in _COMPARABLE_FIELDS) and (
        _cost_profile(first) == _cost_profile(second)
    )


def _cost_profile(run: dict[str, object]) -> dict[str, object]:
    strategy = run["strategy_config"]
    if not isinstance(strategy, dict) or not isinstance(strategy["risk"], dict):
        raise ValueError("invalid persisted strategy risk profile")
    return {
        key: value
        for key, value in strategy["risk"].items()
        if key not in ("target_mode", "reward_numerator", "reward_denominator")
    }
