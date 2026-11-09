from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from random import Random

from aurum.statistics import block_sample, quantile

MONTE_CARLO_VERSION = 1
REFERENCE_WIN_RATES_BPS = (3_000, 3_500, 4_000, 4_500, 5_000, 6_000)
_HORIZONS = (100, 500, 1_000)


class SamplingMode(StrEnum):
    SHUFFLE = "shuffle"
    BLOCK_BOOTSTRAP = "block_bootstrap"
    SCENARIO = "scenario"


@dataclass(frozen=True, slots=True)
class MonteCarloConfig:
    paths: int = 1_000
    trades: int = 1_000
    seed: int = 42
    block_length: int = 5
    initial_equity_cents: int = 2_500_000
    loss_allowance_cents: int = 100_000

    def __post_init__(self) -> None:
        if min(self.paths, self.trades, self.block_length, self.initial_equity_cents) < 1:
            raise ValueError("simulation paths, trades, block length and equity must be positive")
        if not 0 < self.loss_allowance_cents <= self.initial_equity_cents:
            raise ValueError(
                "simulation loss allowance must be positive and no greater than equity"
            )


@dataclass(frozen=True, slots=True)
class WinRateScenario:
    win_rate_bps: int
    gross_win_cents: int = 10_000
    gross_loss_cents: int = 5_000
    minimum_cost_cents: int = 260
    maximum_cost_cents: int = 260

    def __post_init__(self) -> None:
        if not 0 <= self.win_rate_bps <= 10_000:
            raise ValueError("assumed win rate must be between 0 and 10000 basis points")
        if min(self.gross_win_cents, self.gross_loss_cents) < 1 or not (
            0 <= self.minimum_cost_cents <= self.maximum_cost_cents
        ):
            raise ValueError("invalid scenario payout or cost assumptions")


def simulate(
    config: MonteCarloConfig,
    mode: SamplingMode,
    observations: Sequence[int] = (),
    scenario: WinRateScenario | None = None,
) -> dict[str, object]:
    if mode is SamplingMode.SCENARIO:
        if scenario is None or observations:
            raise ValueError("scenario mode requires assumed payouts and no observed outcomes")
    elif scenario is not None or not observations:
        raise ValueError("resampling requires completed backtest observations")
    if mode is SamplingMode.SHUFFLE and config.trades > len(observations):
        raise ValueError("shuffle cannot sample more trades than observed without replacement")

    effective_block = min(config.block_length, len(observations), config.trades)
    rng = Random(config.seed)
    endings, drawdowns, streaks, breach_times = [], [], [], []
    for _ in range(config.paths):
        if scenario is not None:
            moves = _scenario_returns(scenario, config.trades, rng)
        elif mode is SamplingMode.SHUFFLE:
            moves = rng.sample(observations, config.trades)
        else:
            moves = block_sample(observations, config.trades, effective_block, rng)
        ending, drawdown, streak, breach = _path(moves, config)
        endings.append(ending)
        drawdowns.append(drawdown)
        streaks.append(streak)
        if breach is not None:
            breach_times.append(breach)

    return {
        "monte_carlo_version": MONTE_CARLO_VERSION,
        "mode": mode,
        "data_basis": "assumed_outcomes" if scenario else "resampled_backtest",
        "observations": len(observations),
        "config": asdict(config),
        "scenario": asdict(scenario) if scenario else None,
        "effective_block_length": effective_block if mode is SamplingMode.BLOCK_BOOTSTRAP else None,
        "breach_rule": "fixed_floor_reached_or_crossed_after_trade",
        "ending_equity_cents": _distribution(endings),
        "maximum_drawdown_cents": _distribution(drawdowns),
        "maximum_losing_streak": _distribution(streaks),
        "breach_probability": len(breach_times) / config.paths,
        "breached_paths": len(breach_times),
        "time_to_breach_trades": _distribution(breach_times) if breach_times else None,
        "survival": {
            str(horizon): 1 - sum(breach <= horizon for breach in breach_times) / config.paths
            if horizon <= config.trades
            else None
            for horizon in _HORIZONS
        },
    }


def _path(moves: Iterable[int], config: MonteCarloConfig) -> tuple[int, int, int, int | None]:
    equity = peak = config.initial_equity_cents
    floor = equity - config.loss_allowance_cents
    worst_drawdown = streak = max_streak = 0
    breach = None
    for index, move in enumerate(moves, start=1):
        equity += move
        peak = max(peak, equity)
        worst_drawdown = max(worst_drawdown, peak - equity)
        streak = streak + 1 if move < 0 else 0
        max_streak = max(max_streak, streak)
        if breach is None and equity <= floor:
            breach = index
    return equity, worst_drawdown, max_streak, breach


def _scenario_returns(scenario: WinRateScenario, count: int, rng: Random) -> Iterable[int]:
    for _ in range(count):
        won = rng.randrange(10_000) < scenario.win_rate_bps
        gross = scenario.gross_win_cents if won else -scenario.gross_loss_cents
        cost = rng.randint(scenario.minimum_cost_cents, scenario.maximum_cost_cents)
        yield gross - cost


def _distribution(values: list[int]) -> dict[str, object]:
    values.sort()
    return {
        "mean": sum(values) / len(values),
        "median": quantile(values, 0.5),
        "quantiles": {str(p): quantile(values, p / 100) for p in (5, 25, 50, 75, 95)},
        "min": values[0],
        "max": values[-1],
    }
