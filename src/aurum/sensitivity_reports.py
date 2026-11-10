"""Read one-factor perturbations from immutable pre-holdout validation evidence."""

from copy import deepcopy
from datetime import datetime
from enum import StrEnum
from typing import Any

from aurum.market_data import canonical_json
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE

SENSITIVITY_VERSION = 1


class SensitivityFactor(StrEnum):
    VARIANT = "strategy_variant"
    INTERNAL_PIVOT = "internal_pivot"
    EXTERNAL_PIVOT = "external_pivot"
    BREAK_MODE = "break_mode"
    MIN_GAP_TICKS = "minimum_gap_ticks"
    INVERSION_CLOSES = "inversion_closes"
    MINIMUM_SCORE = "minimum_score"
    VOLATILITY_FLOOR = "minimum_volatility_bps"
    VOLATILITY_CEILING = "maximum_volatility_bps"
    SESSIONS = "allowed_sessions"
    REGIMES = "allowed_regimes"
    RISK_BUDGET = "gross_risk_cents"


_CONFIG_PATHS = {
    SensitivityFactor.VARIANT: ("strategy_config", "variant"),
    SensitivityFactor.INTERNAL_PIVOT: ("config", "internal"),
    SensitivityFactor.EXTERNAL_PIVOT: ("config", "external"),
    SensitivityFactor.BREAK_MODE: ("config", "breaks", "mode"),
    SensitivityFactor.MIN_GAP_TICKS: ("evidence_config", "imbalances", "min_width_ticks"),
    SensitivityFactor.INVERSION_CLOSES: ("evidence_config", "imbalances", "inversion_closes"),
    SensitivityFactor.MINIMUM_SCORE: ("strategy_config", "minimum_score"),
    SensitivityFactor.VOLATILITY_FLOOR: ("strategy_config", "minimum_volatility_bps"),
    SensitivityFactor.VOLATILITY_CEILING: ("strategy_config", "maximum_volatility_bps"),
    SensitivityFactor.SESSIONS: ("strategy_config", "allowed_sessions"),
    SensitivityFactor.REGIMES: ("strategy_config", "allowed_regimes"),
    SensitivityFactor.RISK_BUDGET: ("strategy_config", "risk", "gross_allowance_cents"),
}
_METRICS = (
    "trades",
    "wins",
    "losses",
    "break_even",
    "win_rate",
    "net_cents",
    "expectancy_cents",
    "first_decision",
    "last_outcome",
)


def _split_factor(profile: dict[str, Any], path: tuple[str, ...]) -> tuple[dict[str, Any], object]:
    remaining = deepcopy(profile)
    branch = remaining
    for key in path[:-1]:
        child = branch.get(key)
        if not isinstance(child, dict):
            raise ValueError("factor is absent from a saved candidate profile")
        branch = child
    if path[-1] not in branch:
        raise ValueError("factor is absent from a saved candidate profile")
    return remaining, branch.pop(path[-1])


def _outcomes(section: dict[str, object]) -> dict[str, object]:
    return {key: section[key] for key in _METRICS}


def _validation_result(store: DatasetStore, validation_id: str) -> dict[str, Any]:
    report = store.describe_analysis(validation_id)
    result = report["result"]
    if report["kind"] != "chronological_validation" or not isinstance(result, dict):
        raise ValueError("parameter sensitivity requires a chronological validation report")
    return result


def sensitivity_candidate_ids(store: DatasetStore, validation_id: str) -> list[str]:
    return sorted(_validation_result(store, validation_id)["candidates"])


def save_parameter_sensitivity(
    store: DatasetStore,
    validation_id: str,
    baseline_run_id: str,
    factor: SensitivityFactor,
    *,
    run_ids: tuple[str, ...] = (),
) -> str:
    result = _validation_result(store, validation_id)
    profiles, candidates = result["candidate_configs"], result["candidates"]
    compared = run_ids or tuple(candidates)
    if len(compared) < 2 or len(set(compared)) != len(compared):
        raise ValueError("provide at least two distinct validation candidates")
    if baseline_run_id not in compared or any(run_id not in candidates for run_id in compared):
        raise ValueError("baseline and comparison runs must be candidates in the validation report")

    path = _CONFIG_PATHS[factor]
    baseline, _ = _split_factor(profiles[baseline_run_id], path)
    base_expectancy = candidates[baseline_run_id]["validation"]["expectancy_cents"]
    seen: set[str] = set()
    rows = []
    for run_id in (baseline_run_id, *sorted(set(compared) - {baseline_run_id})):
        profile, value = _split_factor(profiles[run_id], path)
        if profile != baseline:
            raise ValueError("candidate profiles differ outside the selected factor")
        encoded = canonical_json(value)
        if encoded in seen:
            raise ValueError("each sensitivity candidate must have a distinct factor value")
        seen.add(encoded)
        candidate = candidates[run_id]
        validation = _outcomes(candidate["validation"])
        expectancy = validation["expectancy_cents"]
        rows.append(
            {
                "run_id": run_id,
                "factor_value": value,
                "is_baseline": run_id == baseline_run_id,
                "selected_on_validation": run_id == result["selected_run_id"],
                "development": _outcomes(candidate["development"]),
                "validation": validation,
                "validation_expectancy_difference_cents": (
                    expectancy - base_expectancy
                    if isinstance(expectancy, int | float)
                    and isinstance(base_expectancy, int | float)
                    else None
                ),
                "validation_uncertainty": candidate["validation_uncertainty"],
                "validation_holm_adjusted_p": result["validation_holm_adjusted_p"].get(run_id),
            }
        )

    start = datetime.fromisoformat(result["validation_start"])
    validation_open = start + result["embargo_minutes"] * MINUTE
    return store.save_analysis(
        "parameter_sensitivity",
        {
            "sensitivity_version": SENSITIVITY_VERSION,
            "validation_id": validation_id,
            "baseline_run_id": baseline_run_id,
            "compared_run_ids": sorted(compared),
            "factor": factor,
            "factor_path": path,
        },
        {
            "sample_kind": "development_and_validation",
            "data_kind": result["data_kind"],
            "dataset": result["dataset"],
            "development_start": result["first_open"],
            "development_end_exclusive": result["validation_start"],
            "validation_start": validation_open.isoformat(),
            "validation_end_exclusive": result["holdout_start"],
            "minimum_selection_trades": result["minimum_selection_trades"],
            "validation_version": result["validation_version"],
            "execution_config": result["execution_config"],
            "baseline_profile": profiles[baseline_run_id],
            "candidates": rows,
        },
    )
