import json
import subprocess
from datetime import timedelta
from pathlib import Path
from sys import executable

import pytest

from aurum.monte_carlo import MonteCarloConfig, SamplingMode, WinRateScenario, simulate
from aurum.statistics import (
    Candidate,
    Observation,
    ValidationConfig,
    bootstrap_expectancy,
    cost_sensitivity,
    evaluate,
    select_period,
)
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-01-01T00:00Z")
DAY = timedelta(days=1)


def synthetic_trade(
    label: str, day: int, pnl: int, *, outcome_day: int | None = None
) -> Observation:
    signal = START + DAY * day
    outcome = START + DAY * outcome_day if outcome_day is not None else signal + 30 * MINUTE
    return Observation(
        label, signal, signal + MINUTE, outcome, pnl, 2, "new_york", "ranging", "bullish", 75
    )


def test_purge_cross_boundary_and_keep_unseen_outcome_out_of_selection() -> None:
    a = Candidate(
        "a",
        (
            synthetic_trade("d", 2, 10),
            synthetic_trade("cross", 9, 9000, outcome_day=10),
            synthetic_trade("embargo", 10, 9000),
            synthetic_trade("v", 11, 25),
            synthetic_trade("h", 22, -9999),
        ),
    )
    b = Candidate(
        "b",
        (
            synthetic_trade("d", 2, 40),
            synthetic_trade("v", 11, -10),
            synthetic_trade("h", 22, 99999),
        ),
    )
    config = ValidationConfig(
        START + 10 * DAY,
        START + 20 * DAY,
        embargo_minutes=10,
        minimum_selection_trades=1,
        bootstrap_draws=20,
        walk_train_days=5,
        walk_test_days=5,
    )
    result = evaluate((a, b), START, START + 30 * DAY, config)
    assert result["selected_run_id"] == "a"
    assert result["purged_or_embargoed"] == {"a": 2, "b": 0}
    holdout = result["holdout"]
    assert isinstance(holdout, dict)
    assert holdout["summary"]["net_cents"] == -9999
    assert holdout["used_for_selection"] is False
    assert holdout["cost_sensitivity_fixed_fills"][1]["net_cents"] == -10099
    assert holdout["summary"]["by_score_decile"]["70"]["trades"] == 1
    windows = result["walk_forward_before_holdout"]
    assert isinstance(windows, list)
    assert all(item["test_end"] <= config.holdout_start.isoformat() for item in windows)
    altered = Candidate("a", (*a.observations[:-1], synthetic_trade("h", 22, 800)))
    assert evaluate((altered, b), START, START + 30 * DAY, config)["selected_run_id"] == "a"


def test_zero_samples_and_boundary_availability() -> None:
    config = ValidationConfig(START + DAY, START + 2 * DAY, bootstrap_draws=10)
    result = evaluate((Candidate("empty", ()),), START, START + 3 * DAY, config)
    assert result["selected_run_id"] is None
    assert result["holdout"] is None
    trade = synthetic_trade("cross", 0, 300, outcome_day=1)
    assert select_period((trade,), START, START + DAY) == ()
    assert select_period((trade,), START, START + DAY, include_final_close=True) == (trade,)
    assert cost_sensitivity((), (100,)) == [
        {
            "extra_cost_per_contract_cents": 100,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "break_even": 0,
            "net_cents": 0,
            "expectancy_cents": None,
            "win_rate": None,
        }
    ]


def test_bootstrap_is_reproducible_and_discloses_effective_block() -> None:
    values = (100, 100, -40, -30, 25, 90, -100, 50)
    first = bootstrap_expectancy(values, 100, 6, 73)
    assert first is not None
    assert first.effective_block_length == 4
    assert first == bootstrap_expectancy(values, 100, 6, 73)
    assert first.lower_cents < first.upper_cents
    assert 0 <= first.one_sided_p <= 1
    assert bootstrap_expectancy([42], 100, 6, 73) is None


def test_assumed_scenarios_report_breach_and_horizon_from_actual_path_length() -> None:
    config = MonteCarloConfig(paths=4, trades=100)
    outcomes = simulate(config, SamplingMode.SCENARIO, scenario=WinRateScenario(0))
    assert outcomes["data_basis"] == "assumed_outcomes"
    assert outcomes["observations"] == 0
    assert outcomes["breach_probability"] == 1
    assert outcomes["survival"] == {"100": 0.0, "500": None, "1000": None}
    time = outcomes["time_to_breach_trades"]
    assert isinstance(time, dict) and time["median"] == 20
    assert outcomes == simulate(config, SamplingMode.SCENARIO, scenario=WinRateScenario(0))
    winners = simulate(config, SamplingMode.SCENARIO, scenario=WinRateScenario(10_000))
    assert winners["breach_probability"] == 0
    assert winners["time_to_breach_trades"] is None


def test_shuffle_keeps_fixed_returns_and_bootstrap_can_extend() -> None:
    values = (1000, -500, 2000)
    config = MonteCarloConfig(paths=25, trades=3)
    result = simulate(config, SamplingMode.SHUFFLE, values)
    endings = result["ending_equity_cents"]
    assert isinstance(endings, dict)
    assert endings["min"] == endings["max"] == config.initial_equity_cents + 2500
    more = MonteCarloConfig(paths=5, trades=7, block_length=2)
    with pytest.raises(ValueError, match="more trades"):
        simulate(more, SamplingMode.SHUFFLE, values)
    result = simulate(more, SamplingMode.BLOCK_BOOTSTRAP, values)
    assert result["effective_block_length"] == 2
    assert result["observations"] == 3


def test_monte_carlo_cli_persists_assumptions_and_result(tmp_path: Path) -> None:
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(tmp_path / "test.sqlite")]
    args = [*aurum, "monte-carlo", "--paths", "4", "--trades", "100", "--win-rate-bps", "3000"]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["definition"]["scenarios"][0]["win_rate_bps"] == 3000
    assert report["result"]["source"] == {"data_kind": "assumed", "observations": 0}
    assert subprocess.run(args, check=True, capture_output=True, text=True).stdout == result.stdout
    reopened = subprocess.run(
        [*aurum, "report", report["report_id"]], check=True, capture_output=True, text=True
    )
    assert reopened.stdout == result.stdout
