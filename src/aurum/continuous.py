import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp

CONTINUOUS_VERSION = 1
_COMPARABLE = (
    "provider",
    "instrument",
    "data_kind",
    "timeframe_minutes",
    "tick_size",
    "price_basis",
)


class PriceBasis(StrEnum):
    UNADJUSTED = "unadjusted"
    FORWARD_ADJUSTED = "forward_adjusted"


@dataclass(frozen=True, slots=True)
class SeriesSegment:
    dataset_id: str
    span: TradingWindow
    reference_open: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            **self.span.as_dict(),
            "reference_open": self.reference_open.isoformat() if self.reference_open else None,
        }


@dataclass(frozen=True, slots=True)
class SeriesPlan:
    basis: PriceBasis
    segments: tuple[SeriesSegment, ...]

    def __post_init__(self) -> None:
        if not self.segments or self.segments[0].reference_open is not None:
            raise ValueError("continuous series requires segments and no initial roll reference")
        for index, segment in enumerate(self.segments[1:], start=1):
            if self.segments[index - 1].span.end != segment.span.start:
                raise ValueError("continuous segments must meet without overlaps or missing time")
            if self.basis is PriceBasis.FORWARD_ADJUSTED and segment.reference_open is None:
                raise ValueError("forward adjustment requires a completed overlap reference")
            if segment.reference_open is not None and not (
                self.segments[index - 1].span.start
                <= segment.reference_open
                <= segment.span.start - MINUTE
            ):
                raise ValueError("roll reference must be in the previous segment before the switch")

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if (
            not isinstance(value, dict)
            or value.keys() != {"basis", "segments"}
            or not isinstance(value["basis"], str)
            or not isinstance(value["segments"], list)
        ):
            raise ValueError("continuous series plan requires basis and segments")
        return cls(PriceBasis(value["basis"]), tuple(_segment(item) for item in value["segments"]))

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))

    def as_dict(self) -> dict[str, object]:
        return {"basis": self.basis, "segments": [segment.as_dict() for segment in self.segments]}


@dataclass(frozen=True, slots=True)
class MappedSegment:
    segment: SeriesSegment
    contract: str
    offset_ticks: int
    previous_reference_ticks: int | None
    next_reference_ticks: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            **self.segment.as_dict(),
            "contract": self.contract,
            "offset_ticks": self.offset_ticks,
            "previous_reference_ticks": self.previous_reference_ticks,
            "next_reference_ticks": self.next_reference_ticks,
        }


def save_series(store: DatasetStore, plan: SeriesPlan) -> str:
    mapped, provenance = _map(store, plan)
    definition = {"continuous_version": CONTINUOUS_VERSION, "plan": plan.as_dict()}
    return store.save_analysis(
        "continuous_series",
        definition,
        {
            "price_basis": plan.basis,
            "source": provenance,
            "segments": [item.as_dict() for item in mapped],
            "use": "research_series",
        },
    )


def replay_series(
    store: DatasetStore, report_id: str, *, span: TradingWindow | None = None
) -> Iterator[dict[str, object]]:
    report = store.describe_analysis(report_id)
    definition = report["definition"]
    if not isinstance(definition, dict) or report["kind"] != "continuous_series":
        raise ValueError("report is not a continuous series")
    if definition["continuous_version"] != CONTINUOUS_VERSION:
        raise ValueError("unsupported continuous series version")
    plan = SeriesPlan.from_dict(definition["plan"])
    mapped, provenance = _map(store, plan)
    for item in mapped:
        segment = item.segment
        start = max(span.start, segment.span.start) if span is not None else segment.span.start
        end = min(span.end, segment.span.end) if span is not None else segment.span.end
        if start >= end:
            continue
        offset = item.offset_ticks
        calendar = SessionCalendar.from_dict(
            store.describe(segment.dataset_id)["provenance"]["calendar"]
        )
        batches = store.replay_batches(segment.dataset_id, TradingWindow(start, end))
        for candle in (minute for batch in batches for minute in batch):
            session = calendar.session_at(candle.opened_at)
            if session is None:
                raise ValueError("continuous candle has no matching exchange session")
            yield {
                "event": "continuous_research_bar",
                "series_id": report_id,
                "source_dataset_id": segment.dataset_id,
                "data_kind": provenance["data_kind"],
                "price_basis": plan.basis,
                "trade_date": session.trade_date.isoformat(),
                "adjustment_ticks": offset,
                "research_ohlc_ticks": {
                    "open": candle.open_ticks + offset,
                    "high": candle.high_ticks + offset,
                    "low": candle.low_ticks + offset,
                    "close": candle.close_ticks + offset,
                },
                "dated_source": candle.as_dict(),
            }


def _map(
    store: DatasetStore, plan: SeriesPlan
) -> tuple[tuple[MappedSegment, ...], dict[str, object]]:
    mapped: list[MappedSegment] = []
    baseline: dict[str, object] | None = None
    seen_contracts = set()
    previous_calendar: SessionCalendar | None = None
    offset = 0
    for segment in plan.segments:
        dataset = store.describe(segment.dataset_id)
        provenance = dataset["provenance"]
        calendar = SessionCalendar.from_dict(provenance["calendar"])
        if calendar.sessions is None:
            raise ValueError("continuous source requires reviewed dated sessions")
        if not dataset["replayable"]:
            raise ValueError("continuous source dataset is incomplete")
        if segment.span.start < calendar.coverage.start or segment.span.end > calendar.coverage.end:
            raise ValueError("continuous segment exceeds source dataset coverage")
        if not any(
            span.start < segment.span.end and segment.span.start < span.end
            for span in calendar.windows
        ):
            raise ValueError("continuous segment has no covered open trading time")
        comparable = {key: provenance[key] for key in _COMPARABLE}
        comparable["calendar_source"] = calendar.source
        if baseline is None:
            baseline = comparable
        elif comparable != baseline:
            raise ValueError("continuous source datasets have incompatible provenance")
        if previous_calendar is not None:
            _check_overlap(previous_calendar, calendar)
        previous_calendar = calendar

        code = str(provenance["contract"])
        if code in seen_contracts or (mapped and code <= mapped[-1].contract):
            raise ValueError("continuous rolls must advance distinct dated MGC contracts")
        seen_contracts.add(code)
        prior = current = None
        reference = segment.reference_open
        if mapped and reference is not None:
            previous = mapped[-1].segment
            reference_span = TradingWindow(reference, reference + MINUTE)
            prior = _reference(store, previous.dataset_id, reference_span)
            current = _reference(store, segment.dataset_id, reference_span)
            if plan.basis is PriceBasis.FORWARD_ADJUSTED:
                offset += prior - current
        mapped.append(MappedSegment(segment, code, offset, prior, current))
    assert baseline is not None
    return tuple(mapped), baseline


def _reference(store: DatasetStore, dataset_id: str, span: TradingWindow) -> int:
    matches = list(store.replay(dataset_id, span=span))
    if len(matches) != 1:
        raise ValueError("roll requires an overlapping unadjusted close in both datasets")
    return matches[0].close_ticks


def _check_overlap(left: SessionCalendar, right: SessionCalendar) -> None:
    start = max(left.coverage.start, right.coverage.start)
    end = min(left.coverage.end, right.coverage.end)
    if start >= end:
        return

    def covered(calendar: SessionCalendar) -> list[tuple[object, datetime, datetime]]:
        return [
            (session.trade_date, max(start, window.start), min(end, window.end))
            for session in calendar.sessions or ()
            for window in session.windows
            if window.start < end and start < window.end
        ]

    if covered(left) != covered(right):
        raise ValueError("continuous source calendars disagree in the overlap")


def _segment(value: object) -> SeriesSegment:
    required = {"dataset_id", "start", "end"}
    if (
        not isinstance(value, dict)
        or not required <= value.keys() <= required | {"reference_open"}
        or not all(isinstance(value[key], str) for key in required)
        or (
            value.get("reference_open") is not None and not isinstance(value["reference_open"], str)
        )
    ):
        raise ValueError("invalid continuous source segment")
    reference = value.get("reference_open")
    return SeriesSegment(
        value["dataset_id"],
        TradingWindow(minute_timestamp(value["start"]), minute_timestamp(value["end"])),
        minute_timestamp(reference) if reference is not None else None,
    )
