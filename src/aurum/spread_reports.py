"""Point-in-time quote coverage at the references in a saved paper backtest."""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from statistics import median

from aurum.exchange_calendar import SessionCalendar
from aurum.execution import OrderKind
from aurum.quote_costs import QUOTE_COST_PROXY_VERSION, QuoteCostProjection
from aurum.quotes import (
    DEFAULT_QUOTE_MAX_AGE_MS,
    MAX_QUOTE_AGE_MS,
    QUOTE_LOOKUP_VERSION,
    QuoteAssessment,
    QuoteLookup,
    QuoteStatus,
)
from aurum.statistics import quantile
from aurum.storage import DatasetStore
from aurum.strategy import SetupState
from aurum.timestamps import EventTimestamp, event_timestamp

SPREAD_REPORT_VERSION = 1
_ANCHORS = ("setup_decision", "modeled_entry_open", "ordered_bracket_touch")


@dataclass(frozen=True, slots=True)
class _Reference:
    kind: str
    setup_id: str
    at: EventTimestamp


def _references(items: list[tuple[int, dict[str, object]]]) -> Iterator[_Reference]:
    for _, item in items:
        match item["event"]:
            case "paper_setup_transition" if item["state"] == SetupState.PAPER_SIGNAL:
                kind, stamp = "setup_decision", item["confirmed_at"]
            case OrderKind.FILLED:
                kind, stamp = "modeled_entry_open", item["entry_at"]
            case "paper_trade_completed":
                touch = item.get("bracket_touch_print")
                if not isinstance(touch, dict):
                    continue
                kind, stamp = "ordered_bracket_touch", touch["executed_at"]
            case _:
                continue
        yield _Reference(kind, str(item["setup_id"]), event_timestamp(str(stamp)))


@dataclass(slots=True)
class _ObservationSummary:
    counts: Counter[str] = field(default_factory=Counter)
    spreads: list[int] = field(default_factory=list)
    ages: list[int] = field(default_factory=list)
    examples: list[dict[str, object]] = field(default_factory=list)

    def add(self, reference: _Reference, assessment: QuoteAssessment, example_limit: int) -> None:
        self.counts[assessment.status] += 1
        if assessment.status is QuoteStatus.FRESH:
            assert assessment.quote is not None and assessment.age_nanoseconds is not None
            self.spreads.append(assessment.quote.ask_ticks - assessment.quote.bid_ticks)
            self.ages.append(assessment.age_nanoseconds)
        if len(self.examples) < example_limit:
            self.examples.append(
                {
                    "setup_id": reference.setup_id,
                    "at": reference.at.isoformat(),
                    **assessment.as_dict(),
                }
            )

    def as_dict(self) -> dict[str, object]:
        total, fresh = self.counts.total(), len(self.spreads)
        ordered = sorted(self.spreads)
        return {
            "references": total,
            "fresh": fresh,
            "fresh_fraction": fresh / total if total else None,
            "statuses": {name: self.counts[name] for name in QuoteStatus},
            "spread_ticks": (
                {
                    "mean": sum(ordered) / fresh,
                    "median": median(ordered),
                    "p95": quantile(ordered, 0.95),
                    "maximum": ordered[-1],
                }
                if fresh
                else None
            ),
            "median_quote_age_nanoseconds": median(self.ages) if fresh else None,
            "examples": self.examples,
        }


def save_spread_report(
    store: DatasetStore,
    run_id: str,
    quote_series_id: str,
    *,
    maximum_age_ms: int = DEFAULT_QUOTE_MAX_AGE_MS,
    example_limit: int = 10,
    include_quote_cost_proxy: bool = False,
) -> str:
    if not 0 <= example_limit <= 100:
        raise ValueError("spread example limit must be between zero and 100")
    if not 0 <= maximum_age_ms <= MAX_QUOTE_AGE_MS:
        raise ValueError("quote maximum age must be between zero and 86400000 ms")
    run = store.describe_backtest(run_id)
    dataset_id = str(run["dataset_id"])
    provenance = store.describe(dataset_id)["provenance"]
    lookup = QuoteLookup(store, quote_series_id)
    quote_source = lookup.provenance
    if any(provenance[key] != quote_source[key] for key in ("contract", "data_kind", "tick_size")):
        raise ValueError("backtest and quote series must share the dated contract and data kind")
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    summaries = {kind: _ObservationSummary() for kind in _ANCHORS}
    costs = (
        QuoteCostProjection(lookup, calendar, maximum_age_ms, example_limit)
        if include_quote_cost_proxy
        else None
    )
    for batch in store.structure_payload_batches(run_id):
        if costs is not None:
            costs.add_batch(item for _, item in batch if item["event"] == "paper_trade_completed")
        references = list(_references(batch))
        if not references:
            continue
        assessments = lookup.assess_many(
            (ref.at for ref in references), maximum_age_ms, price_calendar=calendar
        )
        for ref, assessment in zip(references, assessments, strict=True):
            summaries[ref.kind].add(ref, assessment, example_limit)
    summary = run["summary"]
    assert isinstance(summary, dict)
    if costs is not None and costs.completed != summary["performance"]["trades"]:
        raise ValueError("spread cost sample count differs from the saved backtest")
    return store.save_analysis(
        "quote_spread_observations",
        {
            "spread_report_version": SPREAD_REPORT_VERSION,
            "quote_lookup_version": QUOTE_LOOKUP_VERSION,
            "run_id": run_id,
            "dataset_id": dataset_id,
            "quote_series_id": quote_series_id,
            "maximum_age_ms": maximum_age_ms,
            "example_limit_per_anchor": example_limit,
            **({"quote_cost_proxy_version": QUOTE_COST_PROXY_VERSION} if costs is not None else {}),
        },
        {
            "sample_kind": "unpartitioned_replay_quote_observations",
            "data_kind": provenance["data_kind"],
            "contract": provenance["contract"],
            "price_provider": provenance["provider"],
            "quote_provider": quote_source["provider"],
            "quote_source_sha256": quote_source["source_sha256"],
            "quote_calendar_version": lookup.calendar.version,
            "price_calendar_version": calendar.version,
            "first_open": summary["first_open"],
            "last_close": summary["last_close"],
            "strategy_version": run["strategy_version"],
            "execution_config": run["execution_config"],
            "anchors": {kind: group.as_dict() for kind, group in summaries.items()},
            **({"quote_cost_proxy": costs.as_dict()} if costs is not None else {}),
        },
    )
