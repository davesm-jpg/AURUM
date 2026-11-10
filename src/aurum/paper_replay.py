from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from aurum.exchange_calendar import TradingWindow
from aurum.execution import ExecutionEvent, JournalSignal, OrderKind, PaperExecution, PaperTrade
from aurum.market_data import Candle
from aurum.performance import summarize
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE


@dataclass(slots=True)
class PaperReplayPath:
    engine: PaperExecution
    audit: bool = False
    trades: list[PaperTrade] = field(default_factory=list)
    selected: list[dict[str, object]] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    cancellations: Counter[str] = field(default_factory=Counter)
    admitted: int = 0

    @property
    def active(self) -> bool:
        return self.engine.pending_setup_id is not None or self.engine.open_setup_id is not None

    def record(self, event: ExecutionEvent) -> None:
        if self.audit:
            self.events.append(event.as_dict())
        if isinstance(event, PaperTrade):
            self.trades.append(event)
        elif event.kind is OrderKind.CANCELED and event.reason is not None:
            self.cancellations[event.reason] += 1

    def advance(self, candle: Candle, signals: Iterable[JournalSignal]) -> None:
        for event in self.engine.advance(candle):
            self.record(event)
        for signal in signals:
            self.admitted += 1
            if self.audit:
                self.selected.append(
                    {
                        "setup_id": signal.setup_id,
                        "decision_at": signal.confirmed_at.isoformat(),
                        "direction": signal.direction,
                        "session": signal.context.session,
                        "regime": signal.context.regime,
                        "plan": signal.plan.as_dict(),
                    }
                )
            self.record(self.engine.submit(signal))

    def summary(self, starting_equity: int) -> dict[str, object]:
        stats = summarize(self.trades, starting_equity)
        return {
            "admitted_signals": self.admitted,
            "cancellations": dict(sorted(self.cancellations.items())),
            **{
                key: stats[key]
                for key in (
                    "trades",
                    "wins",
                    "losses",
                    "break_even",
                    "win_rate",
                    "net_profit_cents",
                    "expectancy_cents",
                    "max_drawdown_cents",
                    "total_commission_cents",
                    "total_slippage_cents",
                    "total_spread_cents",
                    "ambiguous_exits",
                )
            },
        }


def replay_scheduled_paths(
    store: DatasetStore,
    dataset_id: str,
    paths: Sequence[PaperReplayPath],
    scheduled: dict[datetime, dict[int, list[JournalSignal]]],
    end: datetime,
) -> None:
    """Advance fresh paper paths only where a decision or open state needs a source minute."""
    if scheduled:
        first = min(scheduled) - MINUTE
        active: set[int] = set()
        for batch in store.replay_batches(dataset_id, TradingWindow(first, end)):
            for candle in batch:
                ready = scheduled.pop(candle.available_at, {})
                for index in active | ready.keys():
                    path = paths[index]
                    path.advance(candle, ready.get(index, ()))
                    if path.active:
                        active.add(index)
                    else:
                        active.discard(index)
        if scheduled:
            raise ValueError("a stored paper decision has no source minute close")
    for path in paths:
        for event in path.engine.finish():
            path.record(event)
