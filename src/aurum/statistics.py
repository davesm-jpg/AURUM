from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from itertools import pairwise
from math import sqrt
from random import Random

VALIDATION_VERSION = 1


@dataclass(frozen=True, slots=True)
class Observation:
    setup_id: str
    decision_at: datetime
    entry_at: datetime
    observed_at: datetime
    net_cents: int
    quantity: int
    session: str
    regime: str
    direction: str
    score: int | None

    def __post_init__(self) -> None:
        stamps = (self.decision_at, self.entry_at, self.observed_at)
        if any(stamp.tzinfo is not UTC for stamp in stamps) or not (
            self.decision_at <= self.entry_at <= self.observed_at
        ):
            raise ValueError("observation requires chronological UTC timestamps")
        if self.quantity < 1 or (self.score is not None and not 0 <= self.score <= 100):
            raise ValueError("invalid trade quantity or score")


@dataclass(frozen=True, slots=True)
class Candidate:
    run_id: str
    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        if any(left.entry_at > right.entry_at for left, right in pairwise(self.observations)):
            raise ValueError("observations must be in chronological entry order")
        if len({item.setup_id for item in self.observations}) != len(self.observations):
            raise ValueError("observations must have distinct setup IDs")


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    validation_start: datetime
    holdout_start: datetime
    embargo_minutes: int = 0
    minimum_selection_trades: int = 20
    bootstrap_draws: int = 2_000
    block_length: int = 5
    seed: int = 42
    walk_train_days: int = 90
    walk_test_days: int = 30
    extra_cost_per_contract_cents: tuple[int, ...] = (0, 50, 100, 200)

    def __post_init__(self) -> None:
        if (
            self.validation_start.tzinfo is not UTC
            or self.holdout_start.tzinfo is not UTC
            or self.validation_start >= self.holdout_start
        ):
            raise ValueError("validation and holdout boundaries must be ordered UTC timestamps")
        if (
            self.embargo_minutes < 0
            or min(
                self.minimum_selection_trades,
                self.block_length,
                self.walk_train_days,
                self.walk_test_days,
            )
            < 1
            or self.bootstrap_draws < 2
            or any(value < 0 for value in self.extra_cost_per_contract_cents)
        ):
            raise ValueError("invalid validation sample, resampling or cost settings")


def evaluate(
    candidates: Sequence[Candidate],
    first_open: datetime,
    last_close: datetime,
    config: ValidationConfig,
) -> dict[str, object]:
    if not candidates or len({candidate.run_id for candidate in candidates}) != len(candidates):
        raise ValueError("validation requires distinct candidate runs")
    if (
        first_open.tzinfo is not UTC
        or last_close.tzinfo is not UTC
        or not first_open < config.validation_start < config.holdout_start < last_close
    ):
        raise ValueError("validation and holdout boundaries must be inside the sample period")
    if any(
        item.decision_at < first_open or item.observed_at > last_close
        for candidate in candidates
        for item in candidate.observations
    ):
        raise ValueError("trade observation is outside the sample period")

    embargo = timedelta(minutes=config.embargo_minutes)
    dev: dict[str, tuple[Observation, ...]] = {}
    validation: dict[str, tuple[Observation, ...]] = {}
    holdout: dict[str, tuple[Observation, ...]] = {}
    for candidate in candidates:
        items = candidate.observations
        dev[candidate.run_id] = select_period(items, first_open, config.validation_start)
        validation[candidate.run_id] = select_period(
            items, config.validation_start + embargo, config.holdout_start
        )
        holdout[candidate.run_id] = select_period(
            items, config.holdout_start + embargo, last_close, include_final_close=True
        )

    chosen = _choose(validation, config.minimum_selection_trades)
    candidate_reports = {}
    p_values = {}
    for run_id in sorted(validation):
        observed = validation[run_id]
        uncertainty = bootstrap_expectancy(
            [item.net_cents for item in observed],
            config.bootstrap_draws,
            config.block_length,
            config.seed,
        )
        if uncertainty is not None:
            p_values[run_id] = uncertainty.one_sided_p
        candidate_reports[run_id] = {
            "development": outcome_summary(dev[run_id]),
            "validation": outcome_summary(observed),
            "validation_uncertainty": uncertainty.as_dict() if uncertainty else None,
        }
    final = None
    if chosen is not None:
        sample = holdout[chosen]
        uncertainty = bootstrap_expectancy(
            [item.net_cents for item in sample],
            config.bootstrap_draws,
            config.block_length,
            config.seed,
        )
        final = {
            "run_id": chosen,
            "used_for_selection": False,
            "summary": outcome_summary(sample),
            "uncertainty": uncertainty.as_dict() if uncertainty else None,
            "cost_sensitivity_fixed_fills": cost_sensitivity(
                sample, config.extra_cost_per_contract_cents
            ),
        }
    return {
        "validation_version": VALIDATION_VERSION,
        "first_open": first_open.isoformat(),
        "last_close": last_close.isoformat(),
        "validation_start": config.validation_start.isoformat(),
        "holdout_start": config.holdout_start.isoformat(),
        "embargo_minutes": config.embargo_minutes,
        "selection_rule": "highest_validation_expectancy_then_run_id",
        "minimum_selection_trades": config.minimum_selection_trades,
        "seed": config.seed,
        "bootstrap_draws": config.bootstrap_draws,
        "block_length_requested": config.block_length,
        "walk_train_days": config.walk_train_days,
        "walk_test_days": config.walk_test_days,
        "selected_run_id": chosen,
        "candidates": candidate_reports,
        "validation_holm_adjusted_p": _holm(p_values),
        "purged_or_embargoed": {
            candidate.run_id: len(candidate.observations)
            - len(dev[candidate.run_id])
            - len(validation[candidate.run_id])
            - len(holdout[candidate.run_id])
            for candidate in candidates
        },
        "holdout": final,
        "walk_forward_before_holdout": _walk_forward(candidates, first_open, config, embargo),
    }


def select_period(
    items: Sequence[Observation],
    start: datetime,
    end: datetime,
    *,
    include_final_close: bool = False,
) -> tuple[Observation, ...]:
    return tuple(
        item
        for item in items
        if start <= item.decision_at < end
        and (item.observed_at < end or (include_final_close and item.observed_at == end))
    )


def outcome_summary(items: Sequence[Observation]) -> dict[str, object]:
    values = [item.net_cents for item in items]
    return {
        **_outcomes(values),
        "first_decision": min(item.decision_at for item in items).isoformat() if items else None,
        "last_outcome": max(item.observed_at for item in items).isoformat() if items else None,
        "by_session": _breakdown((item.session, item.net_cents) for item in items),
        "by_regime": _breakdown((item.regime, item.net_cents) for item in items),
        "by_direction": _breakdown((item.direction, item.net_cents) for item in items),
        "by_score_decile": _breakdown(
            (str(min(item.score // 10, 9) * 10), item.net_cents)
            for item in items
            if item.score is not None
        ),
        "clustering": _clustering(items, values),
    }


def cost_sensitivity(
    items: Sequence[Observation], increments: Sequence[int]
) -> list[dict[str, object]]:
    return [
        {
            "extra_cost_per_contract_cents": cost,
            **_outcomes([item.net_cents - item.quantity * cost for item in items]),
        }
        for cost in increments
    ]


def _outcomes(values: Sequence[int]) -> dict[str, object]:
    count = len(values)
    wins = sum(value > 0 for value in values)
    losses = sum(value < 0 for value in values)
    net = sum(values)
    return {
        "trades": count,
        "wins": wins,
        "losses": losses,
        "break_even": count - wins - losses,
        "net_cents": net,
        "expectancy_cents": net / count if count else None,
        "win_rate": wins / count if count else None,
    }


def _breakdown(pairs: Iterable[tuple[str, int]]) -> dict[str, object]:
    groups: dict[str, list[int]] = defaultdict(list)
    for group, value in pairs:
        groups[group].append(value)
    return {group: _outcomes(values) for group, values in sorted(groups.items())}


def _clustering(items: Sequence[Observation], values: Sequence[int]) -> dict[str, object]:
    day_counts = Counter(item.decision_at.date().isoformat() for item in items)
    left, right = values[:-1], values[1:]
    pairs = len(left)
    left_sum, right_sum = sum(left), sum(right)
    left_var = pairs * sum(value * value for value in left) - left_sum * left_sum
    right_var = pairs * sum(value * value for value in right) - right_sum * right_sum
    correlation = None
    if left_var and right_var:
        covariance = (
            pairs * sum(x * y for x, y in zip(left, right, strict=True)) - left_sum * right_sum
        )
        correlation = covariance / sqrt(left_var * right_var)
    return {
        "trade_counts_by_utc_decision_date": dict(sorted(day_counts.items())),
        "lag_one_net_return_correlation": correlation,
        "lag_one_pairs": pairs,
    }


def _choose(groups: dict[str, tuple[Observation, ...]], minimum: int) -> str | None:
    ranked = [
        (Fraction(sum(item.net_cents for item in sample), len(sample)), run_id)
        for run_id, sample in groups.items()
        if len(sample) >= minimum
    ]
    if not ranked:
        return None
    return min(ranked, key=lambda item: (-item[0], item[1]))[1]


def _walk_forward(
    candidates: Sequence[Candidate], start: datetime, config: ValidationConfig, embargo: timedelta
) -> list[dict[str, object]]:
    train = timedelta(days=config.walk_train_days)
    test = timedelta(days=config.walk_test_days)
    boundary = start + train
    windows: list[dict[str, object]] = []
    while boundary + test <= config.holdout_start:
        eligible = {
            candidate.run_id: select_period(candidate.observations, boundary - train, boundary)
            for candidate in candidates
        }
        winner = _choose(eligible, config.minimum_selection_trades)
        result = None
        if winner is not None:
            selected = next(candidate for candidate in candidates if candidate.run_id == winner)
            result = outcome_summary(
                select_period(selected.observations, boundary + embargo, boundary + test)
            )
        windows.append(
            {
                "train_start": (boundary - train).isoformat(),
                "test_start": boundary.isoformat(),
                "test_end": (boundary + test).isoformat(),
                "selected_run_id": winner,
                "training_sample_count": len(eligible[winner]) if winner is not None else None,
                "test": result,
            }
        )
        boundary += test
    return windows


@dataclass(frozen=True, slots=True)
class BootstrapEstimate:
    observations: int
    draws: int
    effective_block_length: int
    lower_cents: float
    upper_cents: float
    one_sided_p: float

    def as_dict(self) -> dict[str, object]:
        return {
            "method": "circular_moving_block_bootstrap",
            "observations": self.observations,
            "draws": self.draws,
            "effective_block_length": self.effective_block_length,
            "expectancy_95_percentile_cents": [self.lower_cents, self.upper_cents],
            "one_sided_centered_p": self.one_sided_p,
        }


def bootstrap_expectancy(
    values: Sequence[int], draws: int, block_length: int, seed: int
) -> BootstrapEstimate | None:
    if draws < 2 or block_length < 1:
        raise ValueError("bootstrap requires two draws and positive block length")
    count = len(values)
    if count < 2:
        return None
    effective = min(block_length, max(1, count // 2))
    rng = Random(seed)
    means = sorted(sum(block_sample(values, count, effective, rng)) / count for _ in range(draws))
    mean = sum(values) / count
    p_value = (1 + sum(sample_mean - mean >= mean for sample_mean in means)) / (draws + 1)
    return BootstrapEstimate(
        count, draws, effective, quantile(means, 0.025), quantile(means, 0.975), p_value
    )


def block_sample(values: Sequence[int], count: int, length: int, rng: Random) -> Iterable[int]:
    for offset in range(0, count, length):
        start = rng.randrange(len(values))
        for step in range(min(length, count - offset)):
            yield values[(start + step) % len(values)]


def quantile(sorted_values: Sequence[int | float], probability: float) -> float:
    index = (len(sorted_values) - 1) * probability
    lower = int(index)
    fraction = index - lower
    if fraction == 0:
        return float(sorted_values[lower])
    return sorted_values[lower] + fraction * (sorted_values[lower + 1] - sorted_values[lower])


def _holm(values: dict[str, float]) -> dict[str, float]:
    adjusted = {}
    previous = 0.0
    for index, (label, value) in enumerate(
        sorted(values.items(), key=lambda item: (item[1], item[0]))
    ):
        previous = min(1.0, max(previous, value * (len(values) - index)))
        adjusted[label] = previous
    return adjusted
