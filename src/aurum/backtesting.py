from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

from aurum.candles import Timeframe
from aurum.economic_events import EconomicCalendar
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EvidenceConfig
from aurum.exchange_calendar import SessionCalendar
from aurum.execution import (
    DEFAULT_EXECUTION_CONFIG,
    ExecutionConfig,
    ExecutionEvent,
    OrderEvent,
    OrderKind,
    PaperExecution,
    PaperTrade,
)
from aurum.instruments import MgcContract
from aurum.market_context import DEFAULT_CONTEXT_CONFIG, ContextConfig
from aurum.market_data import Candle
from aurum.market_structure import StructureConfig
from aurum.performance import summarize
from aurum.strategy import (
    DEFAULT_STRATEGY_CONFIG,
    SetupEvent,
    SetupState,
    StrategyConfig,
    StrategyReplay,
)

BACKTEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    first_open: datetime | None
    last_close: datetime | None
    signal_count: int
    cancellations: dict[str, int]
    performance: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "event": "paper_backtest_summary",
            "execution_mode": "paper_research",
            "backtest_version": BACKTEST_VERSION,
            "first_open": self.first_open.isoformat() if self.first_open else None,
            "last_close": self.last_close.isoformat() if self.last_close else None,
            "signal_count": self.signal_count,
            "cancellations": self.cancellations,
            "performance": self.performance,
        }


type BacktestEvent = SetupEvent | ExecutionEvent | BacktestSummary


class BacktestReplay:
    def __init__(
        self,
        calendar: SessionCalendar,
        contract: MgcContract,
        timeframe: Timeframe,
        structure: StructureConfig,
        strategy: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
        execution: ExecutionConfig = DEFAULT_EXECUTION_CONFIG,
        evidence: EvidenceConfig = DEFAULT_EVIDENCE_CONFIG,
        context: ContextConfig = DEFAULT_CONTEXT_CONFIG,
        economic: EconomicCalendar | None = None,
    ) -> None:
        self._strategy = StrategyReplay(
            calendar, contract, timeframe, structure, strategy, evidence, context, economic
        )
        self._execution = PaperExecution(calendar, contract, strategy.risk, execution)
        self._config = execution

    def consume(self, candles: Iterable[Candle]) -> Iterator[BacktestEvent]:
        trades: list[PaperTrade] = []
        cancellations: Counter[str] = Counter()
        first_open = last_close = None
        signals = 0

        def record(events: Iterable[ExecutionEvent]) -> Iterator[ExecutionEvent]:
            for event in events:
                if isinstance(event, PaperTrade):
                    trades.append(event)
                elif event.kind is OrderKind.CANCELED and event.reason is not None:
                    cancellations[event.reason] += 1
                yield event

        for candle in candles:
            if first_open is None:
                first_open = candle.opened_at
            last_close = candle.available_at
            yield from record(self._execution.advance(candle))
            for setup in self._strategy.push(candle):
                yield setup
                if setup.state is SetupState.PAPER_SIGNAL:
                    signals += 1
                    order: OrderEvent = self._execution.submit(setup)
                    yield from record((order,))
        yield from record(self._execution.finish())
        yield BacktestSummary(
            first_open,
            last_close,
            signals,
            dict(sorted(cancellations.items())),
            summarize(trades, self._config.initial_equity_cents),
        )
