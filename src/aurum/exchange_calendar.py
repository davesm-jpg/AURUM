import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self

from aurum.timestamps import MINUTE, minute_timestamp


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
class SessionCalendar:
    """Explicit MGC trading windows within a declared, fully reviewed coverage range."""

    source: str
    version: str
    coverage: TradingWindow
    windows: tuple[TradingWindow, ...]

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.version.strip():
            raise ValueError("calendar source and version must be provided")
        previous_end = self.coverage.start
        for window in self.windows:
            if window.start < previous_end or window.end > self.coverage.end:
                raise ValueError("trading windows must be ordered, disjoint, and inside coverage")
            previous_end = window.end

    @classmethod
    def from_json(cls, raw: str) -> Self:
        try:
            document = json.loads(raw, object_pairs_hook=_unique_members)
            if not isinstance(document, dict) or document.keys() != {
                "instrument",
                "source",
                "version",
                "coverage",
                "windows",
            }:
                raise ValueError("calendar requires instrument, source, version, coverage, windows")
            if document["instrument"] != "MGC":
                raise ValueError("calendar instrument must be MGC")
            source, version, windows = document["source"], document["version"], document["windows"]
            if not isinstance(source, str) or not isinstance(version, str):
                raise ValueError("calendar source and version must be text")
            if not isinstance(windows, list):
                raise ValueError("calendar windows must be an array")
            return cls(
                source,
                version,
                _parse_window(document["coverage"]),
                tuple(_parse_window(window) for window in windows),
            )
        except ValueError as exc:
            raise ValueError(f"invalid calendar: {exc}") from exc

    def permits(self, start: datetime) -> bool:
        position = bisect_right(self.windows, start, key=lambda window: window.start) - 1
        return position >= 0 and start + MINUTE <= self.windows[position].end

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": "MGC",
            "source": self.source,
            "version": self.version,
            "coverage": self.coverage.as_dict(),
            "windows": [window.as_dict() for window in self.windows],
        }


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
