import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from re import fullmatch
from typing import Self

from aurum.exchange_calendar import TradingWindow
from aurum.timestamps import minute_timestamp

MAX_ECONOMIC_PUBLICATIONS = 16


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
class EconomicPublication:
    source: str
    version: str
    snapshot_sha256: str
    coverage: TradingWindow
    coverage_known_at: datetime
    coverage_complete: bool
    captures: tuple[EconomicCapture, ...] = ()

    def __post_init__(self) -> None:
        _require_utc(self.coverage_known_at)
        if not self.source.strip() or not self.version.strip():
            raise ValueError("publication source and version are required")
        if fullmatch(r"[a-f0-9]{64}", self.snapshot_sha256) is None:
            raise ValueError("publication requires a normalized snapshot SHA-256")
        _check_capture_provenance(self.captures, self.coverage_known_at, self.coverage_complete)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "version": self.version,
            "snapshot_sha256": self.snapshot_sha256,
            "coverage": self.coverage.as_dict(),
            "coverage_known_at": self.coverage_known_at.isoformat(),
            "coverage_complete": self.coverage_complete,
            **(
                {"captures": [capture.as_dict() for capture in self.captures]}
                if self.captures
                else {}
            ),
        }


@dataclass(frozen=True, slots=True)
class AnnouncementOrigin:
    source: str
    event_id: str

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.event_id.strip():
            raise ValueError("announcement origin requires a source and event ID")

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "event_id": self.event_id}


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
    origin: AnnouncementOrigin | None = None

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
            **({"origin": self.origin.as_dict()} if self.origin is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class EventAssessment:
    risk: EventRisk
    event_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"risk": self.risk, "event_ids": self.event_ids}


@dataclass(frozen=True, slots=True)
class _ReleaseWindow:
    release_at: datetime
    known_at: datetime
    hidden_at: datetime | None
    ordinal: int


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
    publications: tuple[EconomicPublication, ...] = ()
    _release_index: tuple[_ReleaseWindow, ...] = field(init=False, repr=False, compare=False)
    _release_times: tuple[datetime, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_utc(self.coverage_known_at)
        if not self.source.strip() or not self.version.strip():
            raise ValueError("calendar source and version are required")
        _check_capture_provenance(self.captures, self.coverage_known_at, self.coverage_complete)
        if self.captures:
            if self.data_kind is None:
                raise ValueError("compared economic captures require classified coverage")
            if {event.published_at for event in self.events}.difference(
                capture.observed_at for capture in self.captures
            ):
                raise ValueError("compared announcements must reference a capture time")
        self._check_publications()
        by_id = {event.event_id: event for event in self.events}
        if len(by_id) != len(self.events):
            raise ValueError("economic announcement IDs must be unique")
        replaced: dict[str, datetime] = {}
        for event in self.events:
            if event.supersedes is None:
                continue
            previous = by_id.get(event.supersedes)
            if previous is None or previous.published_at >= event.published_at:
                raise ValueError("a revision must replace an earlier published announcement")
            if (
                event.origin is not None
                and previous.origin is not None
                and event.origin.source != previous.origin.source
            ):
                raise ValueError(
                    "announcement revisions cannot replace another publication's events"
                )
            if event.supersedes in replaced:
                raise ValueError("announcement revisions must form a single chronological chain")
            replaced[event.supersedes] = event.published_at
        index = _release_windows(self.events, replaced)
        object.__setattr__(self, "_release_index", index)
        object.__setattr__(self, "_release_times", tuple(window.release_at for window in index))

    def _check_publications(self) -> None:
        if not self.publications:
            if any(event.origin is not None for event in self.events):
                raise ValueError("announcement origins require publication provenance")
            return
        count = len(self.publications)
        sources = {publication.source.strip().casefold() for publication in self.publications}
        if not 2 <= count <= MAX_ECONOMIC_PUBLICATIONS or len(sources) != count:
            raise ValueError("provide distinct economic publications within the supported count")
        if self.captures or self.coverage_complete or self.data_kind is None:
            raise ValueError("composed economic publications require partial, classified coverage")
        expected = TradingWindow(
            min(publication.coverage.start for publication in self.publications),
            max(publication.coverage.end for publication in self.publications),
        )
        if self.coverage != expected or self.coverage_known_at != min(
            publication.coverage_known_at for publication in self.publications
        ):
            raise ValueError("composed coverage does not match the source publication envelope")
        capture_times = {
            publication.source: {capture.observed_at for capture in publication.captures}
            for publication in self.publications
        }
        seen: set[AnnouncementOrigin] = set()
        for event in self.events:
            origin = event.origin
            if origin is None or origin.source not in capture_times or origin in seen:
                raise ValueError(
                    "composed announcements require distinct known publication origins"
                )
            observed = capture_times[origin.source]
            if observed and event.published_at not in observed:
                raise ValueError("composed announcement does not reference a source capture time")
            seen.add(origin)

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        required = {"source", "version", "coverage", "coverage_known_at", "events"}
        optional = {"coverage_complete", "data_kind", "captures", "publications"}
        if (
            not isinstance(payload, dict)
            or not required <= payload.keys() <= required | optional
            or not isinstance(payload["source"], str)
            or not isinstance(payload["version"], str)
            or not isinstance(payload["events"], list)
            or type(payload.get("coverage_complete", True)) is not bool
            or ("data_kind" in payload and not isinstance(payload["data_kind"], str))
        ):
            raise ValueError("invalid economic calendar document")
        publications = payload.get("publications", [])
        if not isinstance(publications, list) or ("publications" in payload and not publications):
            raise ValueError("invalid economic publication provenance")
        return cls(
            payload["source"],
            payload["version"],
            _coverage(payload["coverage"]),
            _event_time(payload["coverage_known_at"]),
            tuple(_announcement(event) for event in payload["events"]),
            payload.get("coverage_complete", True),
            EconomicSourceKind(payload["data_kind"]) if "data_kind" in payload else None,
            _captures(payload),
            tuple(_publication(publication) for publication in publications),
        )

    def assess(self, as_of: datetime, buffer_minutes: int) -> EventAssessment:
        if buffer_minutes < 0:
            raise ValueError("event buffer must be nonnegative")
        buffer = timedelta(minutes=buffer_minutes)
        lower, upper = as_of - buffer, as_of + buffer
        left = bisect_left(self._release_times, lower)
        right = bisect_right(self._release_times, upper)
        nearby = sorted(
            window.ordinal
            for window in self._release_index[left:right]
            if window.known_at <= as_of and (window.hidden_at is None or as_of < window.hidden_at)
        )
        if nearby:
            return EventAssessment(
                EventRisk.ELEVATED, tuple(self.events[index].event_id for index in nearby)
            )
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
            **(
                {"publications": [publication.as_dict() for publication in self.publications]}
                if self.publications
                else {}
            ),
        }


def _release_windows(
    events: tuple[Announcement, ...], replaced: dict[str, datetime]
) -> tuple[_ReleaseWindow, ...]:
    windows = []
    for ordinal, event in enumerate(events):
        if event.cancelled:
            continue
        retired = replaced.get(event.event_id)
        scheduled_until = retired
        actual_known = event.actual_known_at
        if (
            event.actual_at is not None
            and actual_known is not None
            and (retired is None or actual_known < retired)
        ):
            windows.append(_ReleaseWindow(event.actual_at, actual_known, retired, ordinal))
            scheduled_until = actual_known
        if scheduled_until is None or event.published_at < scheduled_until:
            windows.append(
                _ReleaseWindow(event.scheduled_at, event.published_at, scheduled_until, ordinal)
            )
    return tuple(sorted(windows, key=lambda window: window.release_at))


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


def _coverage(span: object) -> TradingWindow:
    if (
        not isinstance(span, dict)
        or span.keys() != {"start", "end"}
        or not isinstance(span["start"], str)
        or not isinstance(span["end"], str)
    ):
        raise ValueError("economic calendar coverage requires timestamp boundaries")
    return TradingWindow(minute_timestamp(span["start"]), minute_timestamp(span["end"]))


def _check_capture_provenance(
    captures: tuple[EconomicCapture, ...], known_at: datetime, complete: bool
) -> None:
    if not captures:
        return
    observed = tuple(capture.observed_at for capture in captures)
    if (
        len(observed) < 2
        or observed[0] != known_at
        or any(older >= newer for older, newer in pairwise(observed))
    ):
        raise ValueError("economic capture provenance requires strictly increasing times")
    if complete:
        raise ValueError("compared economic captures require partial coverage")


def _captures(payload: dict[str, object]) -> tuple[EconomicCapture, ...]:
    values = payload.get("captures", [])
    if not isinstance(values, list) or ("captures" in payload and not values):
        raise ValueError("invalid economic capture provenance")
    return tuple(_capture(value) for value in values)


def _capture(value: object) -> EconomicCapture:
    if (
        not isinstance(value, dict)
        or value.keys() != {"version", "observed_at"}
        or not isinstance(value["version"], str)
    ):
        raise ValueError("invalid economic capture provenance")
    return EconomicCapture(value["version"], _event_time(value["observed_at"]))


def _publication(value: object) -> EconomicPublication:
    required = {
        "source",
        "version",
        "snapshot_sha256",
        "coverage",
        "coverage_known_at",
        "coverage_complete",
    }
    if (
        not isinstance(value, dict)
        or not required <= value.keys() <= required | {"captures"}
        or not isinstance(value["source"], str)
        or not isinstance(value["version"], str)
        or not isinstance(value["snapshot_sha256"], str)
        or type(value["coverage_complete"]) is not bool
    ):
        raise ValueError("invalid economic publication provenance")
    return EconomicPublication(
        value["source"],
        value["version"],
        value["snapshot_sha256"],
        _coverage(value["coverage"]),
        _event_time(value["coverage_known_at"]),
        value["coverage_complete"],
        _captures(value),
    )


def _origin(value: object) -> AnnouncementOrigin:
    if (
        not isinstance(value, dict)
        or value.keys() != {"source", "event_id"}
        or not isinstance(value["source"], str)
        or not isinstance(value["event_id"], str)
    ):
        raise ValueError("invalid economic announcement origin")
    return AnnouncementOrigin(value["source"], value["event_id"])


def _announcement(value: object) -> Announcement:
    required = {"event_id", "title", "scheduled_at", "published_at"}
    optional = {"actual_at", "actual_known_at", "supersedes", "cancelled", "origin"}
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
        _origin(value["origin"]) if "origin" in value else None,
    )
