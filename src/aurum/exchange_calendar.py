import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Self

from aurum.timestamps import MINUTE, minute_timestamp

DATED_SCHEMA_VERSION = 2
COMMON_FIELDS = {"instrument", "source", "version", "coverage"}


@dataclass(frozen=True, slots=True)
class TradingWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for stamp in (self.start, self.end):
            if stamp.tzinfo is not UTC or stamp.second or stamp.microsecond:
                raise ValueError("calendar boundaries must be whole UTC minutes")
        if self.start >= self.end:
            raise ValueError("calendar window start must precede its end")

    def as_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True, slots=True)
class TradingSession:
    trade_date: date
    windows: tuple[TradingWindow, ...]

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("a trading session requires at least one open window")
        for previous, current in pairwise(self.windows):
            if previous.end >= current.start:
                raise ValueError("session windows must be ordered and separated by a closure")

    @property
    def start(self) -> datetime:
        return self.windows[0].start

    @property
    def end(self) -> datetime:
        return self.windows[-1].end

    def as_dict(self) -> dict[str, object]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "windows": [window.as_dict() for window in self.windows],
        }


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    """Reviewed MGC sessions, with open windows clipped to dataset coverage."""

    source: str
    version: str
    coverage: TradingWindow
    windows: tuple[TradingWindow, ...] = ()
    sessions: tuple[TradingSession, ...] | None = None

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.version.strip():
            raise ValueError("calendar source and version must be provided")
        if self.sessions is not None:
            if self.windows:
                raise ValueError("dated calendars derive coverage windows from their sessions")
            object.__setattr__(self, "windows", self._covered_windows(self.sessions))
            return

        previous_end = self.coverage.start
        for window in self.windows:
            if window.start < previous_end or window.end > self.coverage.end:
                raise ValueError("trading windows must be ordered, disjoint, and inside coverage")
            previous_end = window.end

    @classmethod
    def from_json(cls, raw: str) -> Self:
        try:
            return cls.from_dict(json.loads(raw, object_pairs_hook=_unique_members))
        except ValueError as exc:
            raise ValueError(f"invalid calendar: {exc}") from exc

    @classmethod
    def from_dict(cls, document: object) -> Self:
        if not isinstance(document, dict):
            raise ValueError("calendar must be an object")
        if document.keys() == COMMON_FIELDS | {"windows"}:
            dated = False
        elif (
            document.keys() == COMMON_FIELDS | {"schema_version", "sessions"}
            and type(document["schema_version"]) is int
            and document["schema_version"] == DATED_SCHEMA_VERSION
        ):
            dated = True
        else:
            raise ValueError("calendar must use the legacy windows format or schema_version 2")

        if document["instrument"] != "MGC":
            raise ValueError("calendar instrument must be MGC")
        source, version = document["source"], document["version"]
        if not isinstance(source, str) or not isinstance(version, str):
            raise ValueError("calendar source and version must be text")
        coverage = _parse_window(document["coverage"])
        if not dated:
            return cls(source, version, coverage, _parse_windows(document["windows"]))

        sessions = document["sessions"]
        if not isinstance(sessions, list):
            raise ValueError("calendar sessions must be an array")
        return cls(
            source,
            version,
            coverage,
            sessions=tuple(_parse_session(session) for session in sessions),
        )

    def permits(self, start: datetime) -> bool:
        position = bisect_right(self.windows, start, key=lambda window: window.start) - 1
        return position >= 0 and start <= self.windows[position].end - MINUTE

    def session_at(self, start: datetime) -> TradingSession | None:
        """Find the dated session of a covered candle open."""
        if self.sessions is None or not self.permits(start):
            return None
        position = bisect_right(self.sessions, start, key=lambda session: session.start) - 1
        return self.sessions[position]

    def as_dict(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "instrument": "MGC",
            "source": self.source,
            "version": self.version,
            "coverage": self.coverage.as_dict(),
        }
        if self.sessions is None:
            snapshot["windows"] = [window.as_dict() for window in self.windows]
        else:
            snapshot["schema_version"] = DATED_SCHEMA_VERSION
            snapshot["sessions"] = [session.as_dict() for session in self.sessions]
        return snapshot

    def _covered_windows(self, sessions: tuple[TradingSession, ...]) -> tuple[TradingWindow, ...]:
        covered = []
        previous: TradingSession | None = None
        for session in sessions:
            if previous is not None and (
                previous.trade_date >= session.trade_date or previous.end > session.start
            ):
                raise ValueError(
                    "trade dates must be unique and sessions chronological and disjoint"
                )
            if session.end <= self.coverage.start or session.start >= self.coverage.end:
                raise ValueError("trading session must intersect coverage")
            for window in session.windows:
                start = max(window.start, self.coverage.start)
                end = min(window.end, self.coverage.end)
                if start < end:
                    covered.append(TradingWindow(start, end))
            previous = session
        return tuple(covered)


def _parse_session(value: object) -> TradingSession:
    if (
        not isinstance(value, dict)
        or value.keys() != {"trade_date", "windows"}
        or not isinstance(value["trade_date"], str)
    ):
        raise ValueError("calendar session requires a trade_date string and windows")
    trade_date = date.fromisoformat(value["trade_date"])
    if trade_date.isoformat() != value["trade_date"]:
        raise ValueError("trade_date must use YYYY-MM-DD")
    return TradingSession(trade_date, _parse_windows(value["windows"]))


def _parse_windows(value: object) -> tuple[TradingWindow, ...]:
    if not isinstance(value, list):
        raise ValueError("calendar windows must be an array")
    return tuple(_parse_window(window) for window in value)


def _parse_window(value: object) -> TradingWindow:
    if (
        not isinstance(value, dict)
        or value.keys() != {"start", "end"}
        or not isinstance(value["start"], str)
        or not isinstance(value["end"], str)
    ):
        raise ValueError("calendar window requires start and end timestamp strings")
    return TradingWindow(minute_timestamp(value["start"]), minute_timestamp(value["end"]))


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    members: dict[str, object] = {}
    for key, value in pairs:
        if key in members:
            raise ValueError(f"duplicate calendar field: {key}")
        members[key] = value
    return members
