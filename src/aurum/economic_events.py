import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from aurum.exchange_calendar import TradingWindow
from aurum.timestamps import minute_timestamp


class EventRisk(StrEnum):
    UNKNOWN = "unknown"
    CLEAR = "clear"
    ELEVATED = "elevated"


@dataclass(frozen=True, slots=True)
class Announcement:
    event_id: str
    title: str
    scheduled_at: datetime
    published_at: datetime
    actual_at: datetime | None = None
    actual_known_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc(self.scheduled_at, self.published_at, self.actual_at, self.actual_known_at)
        if not self.event_id.strip() or not self.title.strip():
            raise ValueError("announcement identity and title are required")
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

    def __post_init__(self) -> None:
        _require_utc(self.coverage_known_at)
        if not self.source.strip() or not self.version.strip():
            raise ValueError("calendar source and version are required")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("economic announcement IDs must be unique")

    @classmethod
    def from_json(cls, raw: str) -> Self:
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.keys() != {"source", "version", "coverage", "coverage_known_at", "events"}
            or not isinstance(payload["source"], str)
            or not isinstance(payload["version"], str)
            or not isinstance(payload["events"], list)
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
        return cls(
            payload["source"],
            payload["version"],
            coverage,
            _event_time(payload["coverage_known_at"]),
            tuple(_announcement(event) for event in payload["events"]),
        )

    def assess(self, as_of: datetime, buffer_minutes: int) -> EventAssessment:
        if buffer_minutes < 0:
            raise ValueError("event buffer must be nonnegative")
        buffer = timedelta(minutes=buffer_minutes)
        lower, upper = as_of - buffer, as_of + buffer
        nearby = tuple(
            event.event_id
            for event in self.events
            if event.published_at <= as_of and lower <= event.available_release_at(as_of) <= upper
        )
        if nearby:
            return EventAssessment(EventRisk.ELEVATED, nearby)
        if (
            self.coverage_known_at <= as_of
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


def _announcement(value: object) -> Announcement:
    required = {"event_id", "title", "scheduled_at", "published_at"}
    if (
        not isinstance(value, dict)
        or not required <= value.keys() <= required | {"actual_at", "actual_known_at"}
        or not isinstance(value["event_id"], str)
        or not isinstance(value["title"], str)
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
    )
