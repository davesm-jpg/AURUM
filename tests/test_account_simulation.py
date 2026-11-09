import json
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from sys import executable

import pytest

from aurum.account_simulation import (
    LUCIDFLEX_25K,
    AccountMark,
    AccountPhase,
    AccountSimulator,
    AccountStatus,
    BreachMetric,
)
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.monte_carlo import MonteCarloConfig, SamplingMode, TrailingLossScenario, simulate
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-03-10T13:00Z")
DAY = timedelta(days=1)
SESSION_LENGTH = 465 * MINUTE


def synthetic_calendar(*offsets: int) -> SessionCalendar:
    sessions = []
    for offset in offsets:
        opened = START + offset * DAY
        window = TradingWindow(opened, opened + SESSION_LENGTH)
        sessions.append(TradingSession(opened.date(), (window,)))
    return SessionCalendar(
        "synthetic provider-adjusted schedule",
        "fixture-v1",
        TradingWindow(sessions[0].start, sessions[-1].end),
        sessions=tuple(sessions),
    )


def mark(
    day: int,
    *trades: int,
    minutes: int = 0,
    settle: bool = False,
    unrealized: int = 0,
    micros: int = 0,
) -> AccountMark:
    opened = START + day * DAY
    return AccountMark(
        opened + (SESSION_LENGTH if settle else minutes * MINUTE),
        opened.date(),
        trades,
        unrealized,
        micros,
        settle,
    )


def test_intraday_mark_can_breach_previous_eod_floor_without_realized_loss() -> None:
    account = AccountSimulator(synthetic_calendar(0, 1), START)
    intraday = account.observe(mark(0, unrealized=200_000, micros=1))
    assert intraday["loss_floor_cents"] == 2_400_000
    assert intraday["equity_cents"] == 2_700_000
    settled = account.observe(mark(0, 90_000, settle=True))
    assert settled["peak_eod_cents"] == 2_590_000
    assert settled["loss_floor_cents"] == 2_490_000
    breached = account.observe(mark(1, unrealized=-100_000, micros=1))
    assert breached["balance_cents"] == 2_590_000
    assert breached["equity_cents"] == 2_490_000
    assert breached["status"] is AccountStatus.BREACHED
    assert breached["remaining_to_floor_cents"] == 0

    balance_basis = replace(LUCIDFLEX_25K, breach_metric=BreachMetric.BALANCE)
    account = AccountSimulator(synthetic_calendar(0, 1), START, rules=balance_basis)
    account.observe(mark(0, 90_000, settle=True))
    assert account.observe(mark(1, unrealized=-100_000, micros=1))["status"] is AccountStatus.ACTIVE


def test_trailing_floor_locks_at_threshold_and_never_moves_down() -> None:
    account = AccountSimulator(synthetic_calendar(0, 1, 2), START, AccountPhase.FUNDED)
    first = account.observe(mark(0, 110_000, settle=True))
    assert first["loss_floor_cents"] == 2_510_000
    assert first["floor_locked"] is True
    second = account.observe(mark(1, 80_000, settle=True))
    assert second["loss_floor_cents"] == 2_510_000
    assert second["peak_eod_cents"] == 2_690_000
    third = account.observe(mark(2, -90_000, settle=True))
    assert third["loss_floor_cents"] == 2_510_000
    assert third["peak_eod_cents"] == 2_690_000


@pytest.mark.parametrize(
    ("consistency", "days"), [(4000, (50_000, 50_000, 25_000)), (5000, (62_500, 62_500))]
)
def test_evaluation_consistency_and_funded_phase_reset(
    consistency: int, days: tuple[int, ...]
) -> None:
    rules = replace(LUCIDFLEX_25K, evaluation_consistency_bps=consistency)
    account = AccountSimulator(synthetic_calendar(*range(len(days) + 1)), START, rules=rules)
    for index, profit in enumerate(days):
        state = account.observe(mark(index, profit, settle=True))
    assert state["status"] is AccountStatus.EVALUATION_PASSED
    assert state["max_micros"] == 20
    activation = account.activate_funded(START + len(days) * DAY)
    assert activation["phase"] is AccountPhase.FUNDED
    assert activation["balance_cents"] == 2_500_000
    assert activation["loss_floor_cents"] == 2_400_000
    assert activation["max_micros"] == 10


def test_daily_lockout_is_soft_and_resets_at_next_session() -> None:
    rules = replace(LUCIDFLEX_25K, daily_loss_cents=50_000)
    account = AccountSimulator(synthetic_calendar(0, 1), START, rules=rules)
    first = account.observe(mark(0, unrealized=-50_000, micros=1))
    assert first["daily_locked"] is True
    assert first["status"] is AccountStatus.ACTIVE
    assert first["events"] == ("session_started", "daily_loss_lockout")
    account.observe(mark(0, -50_000, settle=True))
    second = account.observe(mark(1))
    assert second["daily_locked"] is False


def test_funded_payout_rules_update_balance_floor_cycle_and_scaling() -> None:
    account = AccountSimulator(synthetic_calendar(0, 1, 2, 3, 4, 5), START, AccountPhase.FUNDED)
    for day in range(4):
        state = account.observe(mark(day, 20_000, settle=True))
    assert state["payout_cap_cents"] == 0
    assert state["max_micros"] == 10
    assert not account.request_payout(mark(3, settle=True).at, 50_000).accepted
    state = account.observe(mark(4, 20_000, settle=True))
    assert state["payout_cap_cents"] == 50_000
    assert state["floor_locked"] is False
    assert state["max_micros"] == 20
    result = account.request_payout(mark(4, settle=True).at, 50_000)
    assert result.accepted and result.trader_cents == 45_000
    after = account.snapshot(mark(4, settle=True).at)
    assert after["balance_cents"] == 2_550_000
    assert after["loss_floor_cents"] == 2_510_000
    assert after["remaining_to_floor_cents"] == 40_000
    assert after["qualifying_payout_days"] == 0
    assert after["payout_cycle_profit_cents"] == 0
    assert after["max_micros"] == 10


def test_account_requires_completed_prior_session_and_monitors_inactivity() -> None:
    account = AccountSimulator(synthetic_calendar(0, 1, 29, 30), START)
    account.observe(mark(0))
    with pytest.raises(ValueError, match="settle the previous"):
        account.observe(mark(1))
    account.observe(mark(0, settle=True))
    account.observe(mark(29, 50, settle=True))
    inactive = account.observe(mark(30))
    assert inactive["status"] is AccountStatus.INACTIVE
    assert inactive["events"] == ("inactivity_threshold",)


def test_trailing_monte_carlo_counts_trade_close_breaches_under_assumptions() -> None:
    config = MonteCarloConfig(paths=30, trades=2)
    moves = (100_000, -100_000)
    fixed = simulate(config, SamplingMode.SHUFFLE, moves)
    eod = simulate(
        config, SamplingMode.SHUFFLE, moves, trailing=TrailingLossScenario(LUCIDFLEX_25K, 1)
    )
    breaches = fixed["breached_paths"]
    assert isinstance(breaches, int) and 0 < breaches < config.paths
    assert eod["breached_paths"] == config.paths
    assert eod["breach_rule"] == "eod_trailing_floor_reached_or_crossed_after_trade"


def test_account_cli_persists_full_synthetic_ledger(tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps(synthetic_calendar(0).as_dict()), encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    document = {
        "source": "synthetic paper observations",
        "data_kind": "synthetic",
        "started_at": START.isoformat(),
        "actions": [
            {
                "kind": "mark",
                "at": START.isoformat(),
                "trade_date": START.date().isoformat(),
                "unrealized_cents": -100_000,
                "open_micros": 1,
            }
        ],
    }
    ledger.write_text(json.dumps(document), encoding="utf-8")
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(tmp_path / "test.sqlite")]
    args = [*aurum, "account", str(ledger), "--calendar", str(calendar)]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["definition"]["ledger"] == document
    assert report["result"]["status"] == "hard_loss_limit_breached"
    assert report["result"]["data_kind"] == "synthetic"
    assert subprocess.run(args, check=True, capture_output=True, text=True).stdout == result.stdout


def test_optional_intraday_payout_scenario_locks_current_session() -> None:
    rules = replace(LUCIDFLEX_25K, daily_loss_cents=50_000, allow_intraday_payouts=True)
    account = AccountSimulator(
        synthetic_calendar(0, 1, 2, 3, 4, 5), START, AccountPhase.FUNDED, rules
    )
    for day in range(5):
        account.observe(mark(day, 20_000, settle=True))
    active = mark(5, 60_000, minutes=10)
    account.observe(active)
    result = account.request_payout(active.at, 50_000)
    assert result.accepted
    assert account.snapshot(active.at)["daily_locked"] is True
    assert account.request_payout(active.at, 50_000).reason == "payout_window_unavailable"
    assert account.observe(mark(5, settle=True))["qualifying_payout_days"] == 0
