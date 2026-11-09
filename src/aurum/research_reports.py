from dataclasses import asdict
from datetime import datetime

from aurum.monte_carlo import (
    MONTE_CARLO_VERSION,
    MonteCarloConfig,
    SamplingMode,
    TrailingLossScenario,
    WinRateScenario,
    simulate,
)
from aurum.statistics import VALIDATION_VERSION, Candidate, Observation, ValidationConfig, evaluate
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
)


def validate_runs(store: DatasetStore, run_ids: tuple[str, ...], config: ValidationConfig) -> str:
    if not run_ids or len(set(run_ids)) != len(run_ids):
        raise ValueError("provide distinct backtest run IDs")
    runs = {run_id: store.describe_backtest(run_id) for run_id in sorted(run_ids)}
    first = next(iter(runs.values()))
    if any(not _comparable(first, run) for run in runs.values()):
        raise ValueError("candidate backtests must share data, evidence and execution assumptions")
    dataset = store.describe(str(first["dataset_id"]))
    definition = {
        **asdict(config),
        "validation_start": config.validation_start.isoformat(),
        "holdout_start": config.holdout_start.isoformat(),
        "validation_version": VALIDATION_VERSION,
        "candidate_run_ids": sorted(run_ids),
        "dataset": _source(dataset["provenance"], str(dataset["dataset_id"])),
    }
    report = evaluate(
        [load_candidate(store, run_id) for run_id in runs],
        datetime.fromisoformat(dataset["first_open"]),
        datetime.fromisoformat(dataset["last_close"]),
        config,
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
    signals: dict[str, tuple[datetime, int | None]] = {}
    observations = []
    for item in store.structure_events(run_id):
        if item["event"] == "paper_setup_transition" and item["state"] == "paper_signal":
            score = item["score"]
            total = score["total"] if isinstance(score, dict) else None
            signals[str(item["setup_id"])] = (
                datetime.fromisoformat(str(item["confirmed_at"])),
                total,
            )
        elif item["event"] == "paper_trade_completed":
            setup = str(item["setup_id"])
            if setup not in signals:
                raise ValueError("backtest trade has no earlier setup signal")
            decision, score_value = signals.pop(setup)
            observations.append(
                Observation(
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
            )
        elif item["event"] == "paper_entry_canceled" and item["reason"] != "duplicate_setup":
            signals.pop(str(item["setup_id"]), None)
    return Candidate(run_id, tuple(observations))


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
