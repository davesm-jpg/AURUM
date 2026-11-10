import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Self

from aurum.exchange_calendar import TradingWindow
from aurum.timestamps import minute_timestamp


class EventRisk(StrEnum):
    UNKNOWN = "unknown"
    CLEAR = "clear"
    ELEVATED = "elevated"


class EconomicSourceKind(StrEnum):
    OBSERVED_PUBLICATION = "observed_publication"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True, slots=True)
class EconomicCapture:
    version: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.observed_at)
        if not self.version.strip():
            raise ValueError("economic capture version is required")

    def as_dict(self) -> dict[str, str]:
        return {"version": self.version, "observed_at": self.observed_at.isoformat()}


@dataclass(frozen=True, slots=True)
class Announcement:
    event_id: str
    title: str
    scheduled_at: datetime
    published_at: datetime
    actual_at: datetime | None = None
    actual_known_at: datetime | None = None
    supersedes: str | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        _require_utc(self.scheduled_at, self.published_at, self.actual_at, self.actual_known_at)
        if not self.event_id.strip() or not self.title.strip():
            raise ValueError("announcement identity and title are required")
        if self.supersedes is not None and not self.supersedes.strip():
            raise ValueError("superseded announcement identity is required")
        if self.cancelled and (self.supersedes is None or self.actual_at is not None):
            raise ValueError("a cancellation must replace an announcement and have no release")
        if (self.actual_at is None) != (self.actual_known_at is None):
            raise ValueError("actual releases require a known-at timestamp")
        if (
            self.actual_at is not None
            and self.actual_known_at is not None
            and self.actual_known_at < max(self.actual_at, self.published_at)
        ):
            raise ValueError("actual release cannot be known before its occurrence or publication")

    def available_release_at(self, as_of: datetime) -> datetime:
        if (
            self.actual_at is not None
            and self.actual_known_at is not None
            and self.actual_known_at <= as_of
        ):
            return self.actual_at
        return self.scheduled_at

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "scheduled_at": self.scheduled_at.isoformat(),
            "published_at": self.published_at.isoformat(),
            "actual_at": self.actual_at.isoformat() if self.actual_at else None,
            "actual_known_at": self.actual_known_at.isoformat() if self.actual_known_at else None,
            **({"supersedes": self.supersedes} if self.supersedes is not None else {}),
            **({"cancelled": True} if self.cancelled else {}),
        }


@dataclass(frozen=True, slots=True)
class EventAssessment:
    risk: EventRisk
    event_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"risk": self.risk, "event_ids": self.event_ids}


@dataclass(frozen=True, slots=True)
class EconomicCalendar:
    source: str
    version: str
    coverage: TradingWindow
    coverage_known_at: datetime
    events: tuple[Announcement, ...]
    coverage_complete: bool = True
    data_kind: EconomicSourceKind | None = None
    captures: tuple[EconomicCapture, ...] = ()

    def __post_init__(self) -> None:
        _require_utc(self.coverage_known_at)
        if not self.source.strip() or not self.version.strip():
            raise ValueError("calendar source and version are required")
        if self.captures:
            observed = tuple(capture.observed_at for capture in self.captures)
            if (
                len(observed) < 2
                or observed[0] != self.coverage_known_at
                or any(older >= newer for older, newer in pairwise(observed))
            ):
                raise ValueError("economic capture provenance requires strictly increasing times")
            if self.coverage_complete or self.data_kind is None:
                raise ValueError("compared economic captures require partial, classified coverage")
            if {event.published_at for event in self.events}.difference(observed):
                raise ValueError("compared announcements must reference a capture time")
        by_id = {event.event_id: event for event in self.events}
        if len(by_id) != len(self.events):
            raise ValueError("economic announcement IDs must be unique")
        replaced: set[str] = set()
        for event in self.events:
            if event.supersedes is None:
                continue
            previous = by_id.get(event.supersedes)
            if previous is None or previous.published_at >= event.published_at:
                raise ValueError("a revision must replace an earlier published announcement")
            if event.supersedes in replaced:
                raise ValueError("announcement revisions must form a single chronological chain")
            replaced.add(event.supersedes)

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        required = {"source", "version", "coverage", "coverage_known_at", "events"}
        if (
            not isinstance(payload, dict)
            or not required
            <= payload.keys()
            <= required | {"coverage_complete", "data_kind", "captures"}
            or not isinstance(payload["source"], str)
            or not isinstance(payload["version"], str)
            or not isinstance(payload["events"], list)
            or type(payload.get("coverage_complete", True)) is not bool
            or ("data_kind" in payload and not isinstance(payload["data_kind"], str))
        ):
            raise ValueError("invalid economic calendar document")
        span = payload["coverage"]
        if (
            not isinstance(span, dict)
            or span.keys() != {"start", "end"}
            or not isinstance(span["start"], str)
            or not isinstance(span["end"], str)
        ):
            raise ValueError("economic calendar coverage requires timestamp boundaries")
        coverage = TradingWindow(minute_timestamp(span["start"]), minute_timestamp(span["end"]))
        captures = payload.get("captures", [])
        if not isinstance(captures, list) or ("captures" in payload and not captures):
            raise ValueError("invalid economic capture provenance")
        return cls(
            payload["source"],
            payload["version"],
            coverage,
            _event_time(payload["coverage_known_at"]),
            tuple(_announcement(event) for event in payload["events"]),
            payload.get("coverage_complete", True),
            EconomicSourceKind(payload["data_kind"]) if "data_kind" in payload else None,
            tuple(_capture(capture) for capture in captures),
        )

    def assess(self, as_of: datetime, buffer_minutes: int) -> EventAssessment:
        if buffer_minutes < 0:
            raise ValueError("event buffer must be nonnegative")
        buffer = timedelta(minutes=buffer_minutes)
        lower, upper = as_of - buffer, as_of + buffer
        superseded = {
            event.supersedes
            for event in self.events
            if event.supersedes is not None and event.published_at <= as_of
        }
        nearby = tuple(
            event.event_id
            for event in self.events
            if event.published_at <= as_of
            and event.event_id not in superseded
            and not event.cancelled
            and lower <= event.available_release_at(as_of) <= upper
        )
        if nearby:
            return EventAssessment(EventRisk.ELEVATED, nearby)
        if (
            self.coverage_complete
            and self.coverage_known_at <= as_of
            and self.coverage.start <= lower <= upper < self.coverage.end
        ):
            return EventAssessment(EventRisk.CLEAR)
        return EventAssessment(EventRisk.UNKNOWN)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "version": self.version,
            "coverage": self.coverage.as_dict(),
            "coverage_known_at": self.coverage_known_at.isoformat(),
            "events": [event.as_dict() for event in self.events],
            **({"coverage_complete": False} if not self.coverage_complete else {}),
            **({"data_kind": self.data_kind} if self.data_kind is not None else {}),
            **(
                {"captures": [capture.as_dict() for capture in self.captures]}
                if self.captures
                else {}
            ),
        }


def _require_utc(*timestamps: datetime | None) -> None:
    if any(stamp is not None and stamp.tzinfo is not UTC for stamp in timestamps):
        raise ValueError("economic event timestamps must be UTC")


def _event_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("economic timestamp must be an ISO date and time with a UTC offset")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("economic timestamp must include a UTC offset")
    return stamp.astimezone(UTC)


def _capture(value: object) -> EconomicCapture:
    if (
        not isinstance(value, dict)
        or value.keys() != {"version", "observed_at"}
        or not isinstance(value["version"], str)
    ):
        raise ValueError("invalid economic capture provenance")
    return EconomicCapture(value["version"], _event_time(value["observed_at"]))


def _announcement(value: object) -> Announcement:
    required = {"event_id", "title", "scheduled_at", "published_at"}
    optional = {"actual_at", "actual_known_at", "supersedes", "cancelled"}
    if (
        not isinstance(value, dict)
        or not required <= value.keys() <= required | optional
        or not isinstance(value["event_id"], str)
        or not isinstance(value["title"], str)
        or value.get("supersedes") is not None
        and not isinstance(value["supersedes"], str)
        or type(value.get("cancelled", False)) is not bool
    ):
        raise ValueError("invalid economic announcement")
    actual, known = value.get("actual_at"), value.get("actual_known_at")
    return Announcement(
        value["event_id"],
        value["title"],
        _event_time(value["scheduled_at"]),
        _event_time(value["published_at"]),
        _event_time(actual) if actual is not None else None,
        _event_time(known) if known is not None else None,
        value.get("supersedes"),
        value.get("cancelled", False),
    )
