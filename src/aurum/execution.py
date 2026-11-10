from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Self

from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_context import Regime, ResearchSession
from aurum.market_data import Candle
from aurum.market_structure import Direction
from aurum.paper_risk import MGC_TICK_CENTS, RiskConfig, RiskPlan, RiskRejection
from aurum.strategy import SetupEvent, SetupState, Variant
from aurum.timestamps import MINUTE

EXECUTION_VERSION = 1


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    latency_minutes: int = 0
    entry_timeout_minutes: int = 5
    round_trip_spread_ticks: int = 0
    carry_across_closures: bool = False
    initial_equity_cents: int = 2_500_000

    def __post_init__(self) -> None:
        if min(self.latency_minutes, self.round_trip_spread_ticks) < 0:
            raise ValueError("latency and spread must be nonnegative")
        if self.entry_timeout_minutes < self.latency_minutes or self.initial_equity_cents < 1:
            raise ValueError("invalid entry timeout or initial paper equity")


DEFAULT_EXECUTION_CONFIG = ExecutionConfig()


class OrderKind(StrEnum):
    QUEUED = "paper_entry_queued"
    FILLED = "paper_entry_filled"
    CANCELED = "paper_entry_canceled"


class ExitReason(StrEnum):
    STOP = "stop"
    TARGET = "target"
    WINDOW_CLOSE = "scheduled_window_close"
    END_OF_DATA = "end_of_data"


@dataclass(frozen=True, slots=True)
class JournalContext:
    session: ResearchSession
    regime: Regime


@dataclass(frozen=True, slots=True)
class JournalSignal:
    setup_id: str
    variant: Variant
    direction: Direction
    confirmed_at: datetime
    plan: RiskPlan
    context: JournalContext
    state: SetupState = SetupState.PAPER_SIGNAL

    @classmethod
    def from_setup_payload(cls, item: dict[str, Any]) -> Self:
        if item["state"] != SetupState.PAPER_SIGNAL:
            raise ValueError("journal signal requires a qualified paper setup")
        raw = item["plan"]
        plan = {field.name: raw[field.name] for field in fields(RiskPlan)}
        plan["direction"] = Direction(plan["direction"])
        if plan["rejection"] is not None:
            plan["rejection"] = RiskRejection(plan["rejection"])
        context = item["context"]
        return cls(
            item["setup_id"],
            Variant(item["variant"]),
            Direction(item["direction"]),
            datetime.fromisoformat(item["confirmed_at"]),
            RiskPlan(**plan),
            JournalContext(ResearchSession(context["session"]), Regime(context["regime"])),
        )


type TradeSignal = SetupEvent | JournalSignal


@dataclass(frozen=True, slots=True)
class OrderEvent:
    kind: OrderKind
    setup_id: str
    recorded_at: datetime
    reason: str | None = None
    entry_at: datetime | None = None
    entry_ticks: int | None = None
    quantity: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "event": self.kind,
            "setup_id": self.setup_id,
            "recorded_at": self.recorded_at.isoformat(),
            "reason": self.reason,
            "entry_at": self.entry_at.isoformat() if self.entry_at else None,
            "entry_ticks": self.entry_ticks,
            "quantity": self.quantity,
            "execution_mode": "paper_research",
            "execution_version": EXECUTION_VERSION,
        }


@dataclass(frozen=True, slots=True)
class PaperTrade:
    signal: TradeSignal
    trade_date: date
    entry_at: datetime
    exit_at: datetime
    recorded_at: datetime
    entry_ticks: int
    exit_ticks: int
    quantity: int
    reason: ExitReason
    ambiguous: bool
    at_open: bool
    mfe_ticks: int
    mae_ticks: int
    commission_cents: int
    slippage_cents: int
    spread_cents: int

    @property
    def gross_cents(self) -> int:
        sign = 1 if self.signal.direction is Direction.BULLISH else -1
        return (self.exit_ticks - self.entry_ticks) * sign * self.quantity * MGC_TICK_CENTS

    @property
    def net_cents(self) -> int:
        return self.gross_cents - self.commission_cents - self.slippage_cents - self.spread_cents

    @property
    def holding_seconds(self) -> int:
        return int((self.exit_at - self.entry_at).total_seconds())

    def as_dict(self) -> dict[str, object]:
        plan = self.signal.plan
        assert plan is not None
        return {
            "event": "paper_trade_completed",
            "setup_id": self.signal.setup_id,
            "state": SetupState.COMPLETED,
            "direction": self.signal.direction,
            "variant": self.signal.variant,
            "session": self.signal.context.session,
            "regime": self.signal.context.regime,
            "trade_date": self.trade_date.isoformat(),
            "entry_at": self.entry_at.isoformat(),
            "exit_at": self.exit_at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "entry_ticks": self.entry_ticks,
            "exit_ticks": self.exit_ticks,
            "stop_ticks": plan.stop_ticks,
            "target_ticks": plan.target_ticks,
            "quantity": self.quantity,
            "reason": self.reason,
            "ambiguous_stop_target": self.ambiguous,
            "exit_at_open": self.at_open,
            "holding_seconds": self.holding_seconds,
            "mfe_ticks": self.mfe_ticks,
            "mae_ticks": self.mae_ticks,
            "gross_cents": self.gross_cents,
            "commission_cents": self.commission_cents,
            "slippage_cents": self.slippage_cents,
            "spread_cents": self.spread_cents,
            "net_cents": self.net_cents,
            "execution_mode": "paper_research",
            "execution_version": EXECUTION_VERSION,
        }


@dataclass(frozen=True, slots=True)
class UnresolvedTrade:
    setup_id: str
    entry_at: datetime
    entry_ticks: int
    quantity: int
    recorded_at: datetime
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "event": "paper_trade_unresolved",
            "setup_id": self.setup_id,
            "entry_at": self.entry_at.isoformat(),
            "entry_ticks": self.entry_ticks,
            "quantity": self.quantity,
            "recorded_at": self.recorded_at.isoformat(),
            "reason": self.reason,
            "execution_mode": "paper_research",
        }


type ExecutionEvent = OrderEvent | PaperTrade
type InterruptionEvent = OrderEvent | UnresolvedTrade


@dataclass(frozen=True, slots=True)
class _PendingOrder:
    signal: TradeSignal
    window_end: datetime
    first_open: datetime
    last_open: datetime


@dataclass(slots=True)
class _Position:
    signal: TradeSignal
    plan: RiskPlan
    trade_date: date
    entry_at: datetime
    entry_ticks: int
    quantity: int
    mfe_ticks: int = 0
    mae_ticks: int = 0

    def observe(self, price: int) -> None:
        move = price - self.entry_ticks
        if self.signal.direction is Direction.BEARISH:
            move = -move
        self.mfe_ticks = max(self.mfe_ticks, move)
        self.mae_ticks = max(self.mae_ticks, -move)


@dataclass(frozen=True, slots=True)
class _Exit:
    price: int
    reason: ExitReason
    at_open: bool = False
    ambiguous: bool = False


class PaperExecution:
    """One position at a time, with minute reference fills and explicit costs."""

    def __init__(
        self,
        calendar: SessionCalendar,
        contract: MgcContract,
        risk: RiskConfig,
        config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG,
    ) -> None:
        if calendar.sessions is None:
            raise ValueError("paper execution requires a dated calendar")
        self._calendar = calendar
        self._contract = contract
        self._risk = risk
        self._config = config
        self._pending: _PendingOrder | None = None
        self._position: _Position | None = None
        self._last: Candle | None = None
        self._recorded_at: datetime | None = None
        self._window: TradingWindow | None = None
        self._closed = False
        self._seen_at_close: set[str] = set()

    @property
    def pending_setup_id(self) -> str | None:
        return self._pending.signal.setup_id if self._pending is not None else None

    @property
    def open_setup_id(self) -> str | None:
        return self._position.signal.setup_id if self._position is not None else None

    def submit(self, signal: TradeSignal, *, observed_at: datetime | None = None) -> OrderEvent:
        if (
            self._closed
            or self._last is None
            or self._window is None
            or signal.confirmed_at != self._last.available_at
        ):
            raise ValueError("paper setups must be submitted at the current minute close")
        observed = signal.confirmed_at if observed_at is None else observed_at
        plan = signal.plan
        if (
            signal.state is not SetupState.PAPER_SIGNAL
            or plan is None
            or plan.quantity < 1
            or plan.target_ticks is None
            or plan.rejection is not None
            or plan.direction is not signal.direction
        ):
            raise ValueError("paper execution requires a qualified setup with a valid bracket")
        self._advance_clock(observed, signal.confirmed_at)
        if signal.setup_id in self._seen_at_close:
            return self._cancel(signal, observed, "duplicate_setup")
        self._seen_at_close.add(signal.setup_id)
        if self._pending is not None or self._position is not None:
            return self._cancel(signal, observed, "position_or_order_open")
        base_open = observed.replace(second=0, microsecond=0)
        if base_open < observed:
            base_open += MINUTE
        first_open = base_open + self._config.latency_minutes * MINUTE
        if first_open >= self._window.end:
            return self._cancel(signal, observed, "entry_window_closed")
        self._pending = _PendingOrder(
            signal,
            self._window.end,
            first_open,
            base_open + self._config.entry_timeout_minutes * MINUTE,
        )
        return OrderEvent(OrderKind.QUEUED, signal.setup_id, observed)

    def advance(
        self, candle: Candle, *, observed_at: datetime | None = None
    ) -> tuple[ExecutionEvent, ...]:
        if self._closed or candle.contract != self._contract:
            raise ValueError("paper execution requires an open simulator and one contract")
        if self._last is not None and candle.opened_at < self._last.available_at:
            raise ValueError("paper execution requires chronological minute closes")
        session = self._calendar.session_at(candle.opened_at)
        if session is None:
            raise ValueError("paper execution candle falls outside covered exchange windows")
        recorded = candle.available_at if observed_at is None else observed_at
        self._advance_clock(recorded, candle.available_at)
        self._window = next(
            window for window in session.windows if window.start <= candle.opened_at < window.end
        )
        self._last = candle
        self._seen_at_close.clear()
        events: list[ExecutionEvent] = []
        if (
            self._pending is not None
            and (entry := self._entry(candle, self._pending, session.trade_date, recorded))
            is not None
        ):
            events.append(entry)
            self._pending = None

        position = self._position
        if position is not None:
            result = _check_bracket(position, candle)
            if result is None or not result.at_open:
                position.observe(candle.open_ticks)
            if result is None:
                position.observe(candle.high_ticks)
                position.observe(candle.low_ticks)
                if (
                    not self._config.carry_across_closures
                    and candle.available_at == self._window.end
                ):
                    result = _Exit(candle.close_ticks, ExitReason.WINDOW_CLOSE)
            if result is not None:
                events.append(self._complete(position, candle, result, recorded))
        return tuple(events)

    def interrupt(self, observed_at: datetime, reason: str) -> tuple[InterruptionEvent, ...]:
        if self._closed:
            raise ValueError("paper execution requires an open simulator")
        self._advance_clock(observed_at, observed_at)
        if self._pending is not None:
            event = self._cancel(self._pending.signal, observed_at, reason)
            self._pending = None
            return (event,)
        if self._position is not None:
            position = self._position
            self._position = None
            return (
                UnresolvedTrade(
                    position.signal.setup_id,
                    position.entry_at,
                    position.entry_ticks,
                    position.quantity,
                    observed_at,
                    reason,
                ),
            )
        return ()

    def finish(self) -> tuple[ExecutionEvent, ...]:
        if self._closed or self._last is None:
            self._closed = True
            return ()
        events: list[ExecutionEvent] = []
        assert self._recorded_at is not None
        if self._pending is not None:
            events.append(self._cancel(self._pending.signal, self._recorded_at, "end_of_data"))
            self._pending = None
        if self._position is not None:
            events.append(
                self._complete(
                    self._position,
                    self._last,
                    _Exit(self._last.close_ticks, ExitReason.END_OF_DATA),
                    self._recorded_at,
                )
            )
        self._closed = True
        return tuple(events)

    def _advance_clock(self, observed: datetime, earliest: datetime) -> None:
        if (
            observed.tzinfo is not UTC
            or observed < earliest
            or (self._recorded_at is not None and observed < self._recorded_at)
        ):
            raise ValueError("paper execution requires a chronological UTC observation clock")
        self._recorded_at = observed

    def _entry(
        self, candle: Candle, order: _PendingOrder, trade_date: date, recorded: datetime
    ) -> OrderEvent | None:
        signal = order.signal
        if candle.opened_at >= order.window_end:
            return self._cancel(signal, recorded, "entry_window_closed")
        if candle.opened_at > order.last_open:
            return self._cancel(signal, recorded, "entry_timed_out")
        if candle.opened_at < order.first_open:
            return None

        plan = signal.plan
        assert plan is not None and plan.target_ticks is not None
        sign = 1 if signal.direction is Direction.BULLISH else -1
        entry = candle.open_ticks
        distance = (entry - plan.stop_ticks) * sign
        if distance < 1 or (plan.target_ticks - entry) * sign < 1:
            return self._cancel(signal, recorded, "entry_outside_bracket")
        gross_risk = distance * MGC_TICK_CENTS
        quantity = min(plan.quantity, self._risk.gross_allowance_cents // gross_risk)
        if self._risk.net_allowance_cents is not None:
            costs = (
                self._risk.commission_round_trip_cents
                + (self._risk.slippage_round_trip_ticks + self._config.round_trip_spread_ticks)
                * MGC_TICK_CENTS
            )
            quantity = min(quantity, self._risk.net_allowance_cents // (gross_risk + costs))
        if quantity < 1:
            return self._cancel(signal, recorded, "entry_risk_exceeded")
        self._position = _Position(signal, plan, trade_date, candle.opened_at, entry, quantity)
        return OrderEvent(
            OrderKind.FILLED,
            signal.setup_id,
            recorded,
            entry_at=candle.opened_at,
            entry_ticks=entry,
            quantity=quantity,
        )

    def _complete(
        self, position: _Position, candle: Candle, result: _Exit, recorded: datetime
    ) -> PaperTrade:
        position.observe(result.price)
        quantity = position.quantity
        self._position = None
        return PaperTrade(
            position.signal,
            position.trade_date,
            position.entry_at,
            candle.opened_at if result.at_open else candle.available_at,
            recorded,
            position.entry_ticks,
            result.price,
            quantity,
            result.reason,
            result.ambiguous,
            result.at_open,
            position.mfe_ticks,
            position.mae_ticks,
            self._risk.commission_round_trip_cents * quantity,
            self._risk.slippage_round_trip_ticks * MGC_TICK_CENTS * quantity,
            self._config.round_trip_spread_ticks * MGC_TICK_CENTS * quantity,
        )

    @staticmethod
    def _cancel(signal: TradeSignal, now: datetime, reason: str) -> OrderEvent:
        return OrderEvent(OrderKind.CANCELED, signal.setup_id, now, reason)


def _check_bracket(position: _Position, candle: Candle) -> _Exit | None:
    stop, target = position.plan.stop_ticks, position.plan.target_ticks
    assert target is not None
    if position.signal.direction is Direction.BULLISH:
        gap_stop, gap_target = candle.open_ticks <= stop, candle.open_ticks >= target
        hit_stop, hit_target = candle.low_ticks <= stop, candle.high_ticks >= target
    else:
        gap_stop, gap_target = candle.open_ticks >= stop, candle.open_ticks <= target
        hit_stop, hit_target = candle.high_ticks >= stop, candle.low_ticks <= target
    if gap_stop:
        return _Exit(candle.open_ticks, ExitReason.STOP, at_open=True)
    if gap_target:
        return _Exit(target, ExitReason.TARGET, at_open=True)
    if hit_stop:
        return _Exit(stop, ExitReason.STOP, ambiguous=hit_target)
    if hit_target:
        return _Exit(target, ExitReason.TARGET)
    return None
