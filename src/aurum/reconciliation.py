"""Compare two immutable normalized imports without lifting research quality gates."""

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from heapq import merge
from itertools import groupby
from operator import attrgetter

from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.market_data import Candle
from aurum.storage import DatasetDescription, DatasetStore
from aurum.timestamps import MINUTE

RECONCILIATION_VERSION = 1
_VALUES = ("open_ticks", "high_ticks", "low_ticks", "close_ticks", "volume")
_REQUIRED_MATCH = (
    "instrument",
    "contract",
    "price_basis",
    "timeframe_minutes",
    "data_type",
    "tick_size",
    "data_kind",
)
_PROVENANCE = (
    *_REQUIRED_MATCH,
    "provider",
    "source_sha256",
    "dataset_version",
    "format",
    "source_timezone",
    "timestamp_label",
)


@dataclass(frozen=True, slots=True)
class _Minute:
    opened_at: datetime
    side: str
    trade_date: date | None
    candle: Candle | None

    def evidence(self) -> dict[str, object]:
        return {
            "trade_date": self.trade_date.isoformat() if self.trade_date is not None else None,
            "ohlcv": (
                {field: getattr(self.candle, field) for field in _VALUES}
                if self.candle is not None
                else None
            ),
        }


def _minutes(
    store: DatasetStore,
    dataset_id: str,
    calendar: SessionCalendar,
    span: TradingWindow,
    side: str,
) -> Iterator[_Minute]:
    candles = (candle for batch in store.inspection_batches(dataset_id, span) for candle in batch)
    candle = next(candles, None)
    for window in calendar.windows:
        opened_at = max(span.start, window.start)
        end = min(span.end, window.end)
        if opened_at >= end:
            continue
        session = calendar.session_at(opened_at)
        trade_date = session.trade_date if session is not None else None
        while opened_at < end:
            if candle is not None and candle.opened_at < opened_at:
                raise ValueError(f"stored {side} candle falls outside its calendar")
            if candle is not None and candle.opened_at == opened_at:
                yield _Minute(opened_at, side, trade_date, candle)
                candle = next(candles, None)
            else:
                yield _Minute(opened_at, side, trade_date, None)
            opened_at += MINUTE
    if candle is not None:
        raise ValueError(f"stored {side} candle falls outside its calendar")


def _scope(
    primary: SessionCalendar, comparison: SessionCalendar, requested: TradingWindow | None
) -> TradingWindow:
    start = max(primary.coverage.start, comparison.coverage.start)
    end = min(primary.coverage.end, comparison.coverage.end)
    if start >= end:
        raise ValueError("dataset calendars have no overlapping coverage")
    if requested is None:
        return TradingWindow(start, end)
    if requested.start < start or requested.end > end:
        raise ValueError("comparison bounds must be within both dataset calendar coverages")
    return requested


def _source(description: DatasetDescription, calendar: SessionCalendar) -> dict[str, object]:
    return {
        "dataset_id": description["dataset_id"],
        "first_open": description["first_open"],
        "last_close": description["last_close"],
        "candle_count": description["candle_count"],
        "replayable": description["replayable"],
        "issues": description["issues"],
        **{field: description["provenance"][field] for field in _PROVENANCE},
        "calendar_source": calendar.source,
        "calendar_version": calendar.version,
        "calendar_coverage": calendar.coverage.as_dict(),
    }


def save_reconciliation(
    store: DatasetStore,
    primary_id: str,
    comparison_id: str,
    *,
    span: TradingWindow | None = None,
    example_limit: int = 20,
) -> str:
    if primary_id == comparison_id:
        raise ValueError("reconciliation requires two distinct imports")
    if not 0 <= example_limit <= 100:
        raise ValueError("reconciliation example limit must be between 0 and 100")
    primary, comparison = store.describe(primary_id), store.describe(comparison_id)
    first, second = primary["provenance"], comparison["provenance"]
    if any(first[field] != second[field] for field in _REQUIRED_MATCH):
        raise ValueError("imports must share contract, bar definition, price basis and data kind")
    primary_cal = SessionCalendar.from_dict(first["calendar"])
    comparison_cal = SessionCalendar.from_dict(second["calendar"])
    window = _scope(primary_cal, comparison_cal, span)
    dated = primary_cal.sessions is not None and comparison_cal.sessions is not None
    counts: Counter[str] = Counter()
    field_mismatches: Counter[str] = Counter()
    examples: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    minute_stream = merge(
        _minutes(store, primary_id, primary_cal, window, "primary"),
        _minutes(store, comparison_id, comparison_cal, window, "comparison"),
        key=attrgetter("opened_at"),
    )
    for opened_at, group in groupby(minute_stream, key=attrgetter("opened_at")):
        sides = {item.side: item for item in group}
        left, right = sides.get("primary"), sides.get("comparison")
        counts["scheduled_in_either"] += 1
        categories: list[str] = []
        differences: list[str] = []
        for name, minute in sides.items():
            counts[f"{name}_scheduled"] += 1
            if minute.candle is None:
                categories.append(f"{name}_missing")
            else:
                counts[f"{name}_observed"] += 1
        if left is None or right is None:
            categories.append("calendar_open_disagreement")
        else:
            counts["scheduled_in_both"] += 1
            if dated and left.trade_date != right.trade_date:
                categories.append("trade_date_disagreement")
            if left.candle is not None and right.candle is not None:
                counts["compared_ohlcv"] += 1
                differences = [
                    name
                    for name in _VALUES
                    if getattr(left.candle, name) != getattr(right.candle, name)
                ]
                field_mismatches.update(differences)
                if differences:
                    categories.append("ohlcv_mismatch")
                else:
                    counts["exact_ohlcv_matches"] += 1
        for category in categories:
            counts[category] += 1
            if len(examples[category]) < example_limit:
                examples[category].append(
                    {
                        "opened_at": opened_at.isoformat(),
                        "primary": left.evidence() if left is not None else None,
                        "comparison": right.evidence() if right is not None else None,
                        "mismatched_fields": differences,
                    }
                )

    discrepancy_fields = (
        "primary_missing",
        "comparison_missing",
        "calendar_open_disagreement",
        "trade_date_disagreement",
        "ohlcv_mismatch",
    )
    passes = (
        dated
        and counts["compared_ohlcv"] > 0
        and not any(counts[field] for field in discrepancy_fields)
    )
    return store.save_analysis(
        "dataset_reconciliation",
        {
            "reconciliation_version": RECONCILIATION_VERSION,
            "primary_dataset_id": primary_id,
            "comparison_dataset_id": comparison_id,
            "window": window.as_dict(),
            "example_limit_per_category": example_limit,
        },
        {
            "data_kind": first["data_kind"],
            "primary": _source(primary, primary_cal),
            "comparison": _source(comparison, comparison_cal),
            "identical_raw_checksum": first["source_sha256"] == second["source_sha256"],
            "trade_dates_comparable": dated,
            "exact_on_window": passes,
            "counts": {
                name: counts[name]
                for name in (
                    "scheduled_in_either",
                    "scheduled_in_both",
                    "primary_scheduled",
                    "comparison_scheduled",
                    "primary_observed",
                    "comparison_observed",
                    "compared_ohlcv",
                    "exact_ohlcv_matches",
                    *discrepancy_fields,
                )
            },
            "ohlcv_field_mismatches": {name: field_mismatches[name] for name in _VALUES},
            "examples": dict(examples),
        },
    )
