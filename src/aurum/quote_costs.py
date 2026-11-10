"""Fixed-fill cost sensitivity from paired preceding quote observations."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from aurum.exchange_calendar import SessionCalendar
from aurum.execution import ExitReason
from aurum.paper_risk import MGC_TICK_CENTS
from aurum.quotes import QuoteAssessment, QuoteLookup, QuoteStatus
from aurum.timestamps import EventTimestamp, event_timestamp

QUOTE_COST_PROXY_VERSION = 1


@dataclass(frozen=True, slots=True)
class _TradeReferences:
    trade: dict[str, object]
    entry: EventTimestamp
    exit: EventTimestamp
    exit_basis: str


def _pair(trade: dict[str, object]) -> _TradeReferences | None:
    touch = trade.get("bracket_touch_print")
    if isinstance(touch, dict):
        at, basis = touch["executed_at"], "ordered_bracket_touch"
    elif trade["exit_at_open"] is True:
        at, basis = trade["exit_at"], "modeled_open"
    elif trade["reason"] in (ExitReason.WINDOW_CLOSE, ExitReason.END_OF_DATA):
        at, basis = trade["exit_at"], "modeled_close"
    else:
        return None
    return _TradeReferences(
        trade, event_timestamp(str(trade["entry_at"])), event_timestamp(str(at)), basis
    )


def _integer(trade: dict[str, object], key: str) -> int:
    value = trade[key]
    if type(value) is not int:
        raise ValueError("quote cost sensitivity requires integer paper trade values")
    return value


@dataclass(slots=True)
class QuoteCostProjection:
    lookup: QuoteLookup
    price_calendar: SessionCalendar
    maximum_age_ms: int
    example_limit: int
    completed: int = 0
    paired: int = 0
    original_spread: int = 0
    observed_spread: int = 0
    original_net: int = 0
    repriced_net: int = 0
    excluded: Counter[str] = field(default_factory=Counter)
    examples: list[dict[str, object]] = field(default_factory=list)

    def add_batch(self, trades: Iterable[dict[str, object]]) -> None:
        pairs = []
        for trade in trades:
            self.completed += 1
            pair = _pair(trade)
            if pair is None:
                self.excluded["exit_time_unknown"] += 1
            else:
                pairs.append(pair)
        if not pairs:
            return
        quotes = self.lookup.assess_many(
            (stamp for pair in pairs for stamp in (pair.entry, pair.exit)),
            self.maximum_age_ms,
            price_calendar=self.price_calendar,
        )
        for pair, entry, exit_quote in zip(pairs, quotes[::2], quotes[1::2], strict=True):
            self._add_pair(pair, entry, exit_quote)

    def _add_pair(
        self, pair: _TradeReferences, entry: QuoteAssessment, exit_quote: QuoteAssessment
    ) -> None:
        if entry.status is not QuoteStatus.FRESH:
            self.excluded[f"entry_{entry.status}"] += 1
            return
        if exit_quote.status is not QuoteStatus.FRESH:
            self.excluded[f"exit_{exit_quote.status}"] += 1
            return
        assert entry.quote is not None and exit_quote.quote is not None
        entry_width = entry.quote.ask_ticks - entry.quote.bid_ticks
        exit_width = exit_quote.quote.ask_ticks - exit_quote.quote.bid_ticks
        quantity = _integer(pair.trade, "quantity")
        recorded_spread = _integer(pair.trade, "spread_cents")
        recorded_net = _integer(pair.trade, "net_cents")
        quoted_proxy = (entry_width + exit_width) * (MGC_TICK_CENTS // 2) * quantity
        adjusted_net = recorded_net + recorded_spread - quoted_proxy
        self.paired += 1
        self.original_spread += recorded_spread
        self.observed_spread += quoted_proxy
        self.original_net += recorded_net
        self.repriced_net += adjusted_net
        if len(self.examples) < self.example_limit:
            self.examples.append(
                {
                    "setup_id": pair.trade["setup_id"],
                    "quantity": quantity,
                    "entry": {"at": pair.entry.isoformat(), **entry.as_dict()},
                    "exit": {
                        "at": pair.exit.isoformat(),
                        "basis": pair.exit_basis,
                        **exit_quote.as_dict(),
                    },
                    "recorded_spread_cents": recorded_spread,
                    "quoted_spread_proxy_cents": quoted_proxy,
                    "recorded_net_cents": recorded_net,
                    "repriced_net_cents": adjusted_net,
                }
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "method": "replace_modeled_spread_with_half_quoted_width_at_each_reference",
            "completed_trades": self.completed,
            "paired_trades": self.paired,
            "paired_fraction": self.paired / self.completed if self.completed else None,
            "excluded_first_reason": dict(sorted(self.excluded.items())),
            "paired_recorded_spread_cents": self.original_spread if self.paired else None,
            "paired_quoted_spread_proxy_cents": self.observed_spread if self.paired else None,
            "paired_recorded_net_cents": self.original_net if self.paired else None,
            "paired_repriced_net_cents": self.repriced_net if self.paired else None,
            "paired_net_difference_cents": (
                self.repriced_net - self.original_net if self.paired else None
            ),
            "examples": self.examples,
        }
