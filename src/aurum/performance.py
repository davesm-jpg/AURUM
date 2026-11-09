from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from math import sqrt
from statistics import median

from aurum.execution import PaperTrade

PERFORMANCE_VERSION = 1
_MILESTONES = (100, 250, 500, 1_000)


def summarize(trades: Sequence[PaperTrade], starting_equity: int) -> dict[str, object]:
    counts = _outcomes(trades)
    net = [trade.net_cents for trade in trades]
    gross = [trade.gross_cents for trade in trades]
    wins = [value for value in net if value > 0]
    losses = [-value for value in net if value < 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    durations = [trade.holding_seconds for trade in trades]
    win_streak, loss_streak = _streaks(net)
    equity = peak = starting_equity
    drawdown = 0
    curve = []
    for trade in trades:
        equity += trade.net_cents
        peak = max(peak, equity)
        below_peak = peak - equity
        drawdown = max(drawdown, below_peak)
        curve.append(
            {
                "recorded_at": trade.recorded_at.isoformat(),
                "equity_cents": equity,
                "drawdown_cents": below_peak,
                "setup_id": trade.signal.setup_id,
            }
        )
    interval = _wilson(len(wins), len(trades))
    return {
        **counts,
        "performance_version": PERFORMANCE_VERSION,
        "gross_profit_cents": sum(value for value in gross if value > 0),
        "gross_loss_cents": -sum(value for value in gross if value < 0),
        "total_commission_cents": sum(trade.commission_cents for trade in trades),
        "total_slippage_cents": sum(trade.slippage_cents for trade in trades),
        "total_spread_cents": sum(trade.spread_cents for trade in trades),
        "average_win_cents": avg_win,
        "average_loss_cents": avg_loss,
        "realized_reward_to_risk": avg_win / avg_loss if avg_win and avg_loss else None,
        "profit_factor": sum(wins) / sum(losses) if losses else None,
        "max_drawdown_cents": drawdown,
        "drawdown_basis": "closed_trade_equity",
        "recovery_factor": sum(net) / drawdown if drawdown else None,
        "max_consecutive_wins": win_streak,
        "max_consecutive_losses": loss_streak,
        "average_holding_seconds": sum(durations) / len(durations) if durations else None,
        "median_holding_seconds": median(durations) if durations else None,
        "max_favorable_excursion_ticks": max((trade.mfe_ticks for trade in trades), default=None),
        "max_adverse_excursion_ticks": max((trade.mae_ticks for trade in trades), default=None),
        "exit_reasons": dict(sorted(Counter(trade.reason for trade in trades).items())),
        "ambiguous_exits": sum(trade.ambiguous for trade in trades),
        "starting_equity_cents": starting_equity,
        "ending_equity_cents": equity,
        "equity_curve": curve,
        "by_month": _group(trades, lambda trade: trade.trade_date.isoformat()[:7]),
        "by_weekday": _group(trades, lambda trade: str(trade.trade_date.isoweekday())),
        "by_session": _group(trades, lambda trade: trade.signal.context.session.value),
        "by_regime": _group(trades, lambda trade: trade.signal.context.regime.value),
        "by_direction": _group(trades, lambda trade: trade.signal.direction.value),
        "net_return_distribution_cents": [
            {"net_cents": value, "count": count} for value, count in sorted(Counter(net).items())
        ],
        "win_rate_wilson_95_iid": interval,
        "observation_milestones": {
            str(size): _outcomes(trades[:size]) for size in _MILESTONES if len(trades) >= size
        },
    }


def _outcomes(trades: Sequence[PaperTrade]) -> dict[str, object]:
    count = len(trades)
    wins = sum(trade.net_cents > 0 for trade in trades)
    losses = sum(trade.net_cents < 0 for trade in trades)
    net = sum(trade.net_cents for trade in trades)
    return {
        "trades": count,
        "wins": wins,
        "losses": losses,
        "break_even": count - wins - losses,
        "win_rate": wins / count if count else None,
        "loss_rate": losses / count if count else None,
        "net_profit_cents": net,
        "expectancy_cents": net / count if count else None,
    }


def _group(
    trades: Sequence[PaperTrade], key: Callable[[PaperTrade], str]
) -> dict[str, dict[str, object]]:
    groups: dict[str, list[PaperTrade]] = defaultdict(list)
    for trade in trades:
        groups[key(trade)].append(trade)
    return {label: _outcomes(group) for label, group in sorted(groups.items())}


def _streaks(values: Sequence[int]) -> tuple[int, int]:
    wins = losses = max_wins = max_losses = 0
    for value in values:
        wins = wins + 1 if value > 0 else 0
        losses = losses + 1 if value < 0 else 0
        max_wins = max(wins, max_wins)
        max_losses = max(losses, max_losses)
    return max_wins, max_losses


def _wilson(wins: int, count: int) -> tuple[float, float] | None:
    if not count:
        return None
    rate = wins / count
    z = 1.959963984540054
    denominator = 1 + z**2 / count
    center = (rate + z**2 / (2 * count)) / denominator
    half = z * sqrt(rate * (1 - rate) / count + z**2 / (4 * count**2)) / denominator
    return max(0.0, center - half), min(1.0, center + half)
