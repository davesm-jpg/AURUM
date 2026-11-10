"""Normalize reviewed local iCalendar captures into causal scheduled-event evidence."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from importlib import import_module
from importlib.metadata import version
from itertools import pairwise
from pathlib import Path
from typing import Any

from aurum.economic_events import (
    Announcement,
    EconomicCalendar,
    EconomicCapture,
    EconomicSourceKind,
)
from aurum.exchange_calendar import TradingWindow
from aurum.market_data import canonical_json
from aurum.timestamps import source_timezone, utc_from_wall_time

MAX_ECONOMIC_ICS_BYTES = 8 * 1024 * 1024
MAX_ECONOMIC_CAPTURES = 64
ECONOMIC_ICS_VERSION = 1
ECONOMIC_COMPARISON_VERSION = 1
_RECURRING = {"RRULE", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID"}


@dataclass(frozen=True, slots=True)
class EconomicIcsInput:
    observed_at: datetime
    read: Callable[[], bytes]
    archive_id: str | None = None

    def parse(self) -> tuple[Any, str]:
        document, version = parse_economic_ics(self.read())
        if self.archive_id is not None:
            version = f"archive-v1/{self.archive_id}/{version}"
        return document, version


def normalize_economic_ics(
    path: Path,
    *,
    source: str,
    coverage: TradingWindow,
    observed_at: datetime,
    data_kind: EconomicSourceKind,
    title_prefixes: tuple[str, ...],
) -> EconomicCalendar:
    return normalize_economic_ics_input(
        EconomicIcsInput(observed_at, partial(_read_bytes, path)),
        source=source,
        coverage=coverage,
        data_kind=data_kind,
        title_prefixes=title_prefixes,
    )


def normalize_economic_ics_input(
    capture: EconomicIcsInput,
    *,
    source: str,
    coverage: TradingWindow,
    data_kind: EconomicSourceKind,
    title_prefixes: tuple[str, ...],
) -> EconomicCalendar:
    if capture.observed_at.tzinfo is not UTC:
        raise ValueError("economic calendar capture time must be UTC")
    prefixes = _prefixes(title_prefixes)
    document, capture_version = capture.parse()
    events = []
    for uid, title, component in _selected_events(document, prefixes, {}):
        if _cancelled(component):
            continue
        scheduled_at = _release_time(component)
        if coverage.start <= scheduled_at < coverage.end:
            events.append(
                Announcement(_digest((source, uid)), title, scheduled_at, capture.observed_at)
            )
    return EconomicCalendar(
        source,
        capture_version,
        coverage,
        capture.observed_at,
        tuple(sorted(events, key=lambda event: (event.scheduled_at, event.event_id))),
        coverage_complete=False,
        data_kind=data_kind,
    )


def compare_economic_ics(
    captures: tuple[tuple[Path, datetime], ...],
    *,
    source: str,
    coverage: TradingWindow,
    data_kind: EconomicSourceKind,
    title_prefixes: tuple[str, ...],
) -> EconomicCalendar:
    return compare_economic_ics_inputs(
        tuple(
            EconomicIcsInput(observed, partial(_read_bytes, path)) for path, observed in captures
        ),
        source=source,
        coverage=coverage,
        data_kind=data_kind,
        title_prefixes=title_prefixes,
    )


def compare_economic_ics_inputs(
    captures: tuple[EconomicIcsInput, ...],
    *,
    source: str,
    coverage: TradingWindow,
    data_kind: EconomicSourceKind,
    title_prefixes: tuple[str, ...],
) -> EconomicCalendar:
    """Build a partial point-in-time snapshot from ordered copies of one publication."""
    if not 2 <= len(captures) <= MAX_ECONOMIC_CAPTURES:
        raise ValueError(f"provide between 2 and {MAX_ECONOMIC_CAPTURES} economic captures")
    observed_times = tuple(capture.observed_at for capture in captures)
    if any(observed.tzinfo is not UTC for observed in observed_times):
        raise ValueError("economic calendar capture time must be UTC")
    if any(older >= newer for older, newer in pairwise(observed_times)):
        raise ValueError("economic captures must be in strictly increasing observation order")

    prefixes = _prefixes(title_prefixes)
    latest: dict[str, Announcement] = {}
    sequences: dict[str, int] = {}
    history = []
    provenance = []
    for capture in captures:
        observed_at = capture.observed_at
        document, capture_version = capture.parse()
        method = document.get("METHOD", "PUBLISH")
        if not isinstance(method, str) or method.upper() != "PUBLISH":
            raise ValueError("comparison requires reviewed calendar publications")
        provenance.append(EconomicCapture(capture_version, observed_at))
        for uid, title, component in _selected_events(document, prefixes, latest):
            prior = latest.get(uid)
            if _RECURRING.intersection(component.keys()):
                raise ValueError("selected economic events must have individual release times")
            cancelled = _cancelled(component)
            if cancelled:
                if prior is None:
                    continue
                title, scheduled_at = prior.title, prior.scheduled_at
            else:
                scheduled_at = _release_time(component)
                if prior is None and not coverage.start <= scheduled_at < coverage.end:
                    continue
            same = prior is not None and (title, scheduled_at, cancelled) == (
                prior.title,
                prior.scheduled_at,
                prior.cancelled,
            )
            sequence = component.get("SEQUENCE")
            if sequence is not None:
                if not isinstance(sequence, int) or sequence < 0:
                    raise ValueError("selected economic event has an invalid SEQUENCE")
                latest_sequence = sequences.get(uid, sequence)
                moved = prior is not None and (scheduled_at, cancelled) != (
                    prior.scheduled_at,
                    prior.cancelled,
                )
                if (sequence < latest_sequence and not same) or (
                    sequence == latest_sequence and uid in sequences and moved
                ):
                    raise ValueError("changed economic event has a stale or conflicting SEQUENCE")
                sequences[uid] = max(sequence, latest_sequence)
            if same:
                continue
            event = Announcement(
                _digest((source, uid, observed_at.isoformat(), capture_version)),
                title,
                scheduled_at,
                observed_at,
                supersedes=prior.event_id if prior else None,
                cancelled=cancelled,
            )
            latest[uid] = event
            history.append(event)

    capture_identity = [capture.as_dict() for capture in provenance]
    comparison = _digest(
        (source, coverage.as_dict(), data_kind, sorted(set(prefixes)), capture_identity)
    )
    return EconomicCalendar(
        source,
        f"ical-compare-v{ECONOMIC_COMPARISON_VERSION}/{comparison}",
        coverage,
        observed_times[0],
        tuple(sorted(history, key=lambda event: (event.published_at, event.event_id))),
        coverage_complete=False,
        data_kind=data_kind,
        captures=tuple(provenance),
    )


def _digest(value: object) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _prefixes(title_prefixes: tuple[str, ...]) -> tuple[str, ...]:
    prefixes = tuple(prefix.strip().casefold() for prefix in title_prefixes)
    if not prefixes or any(not prefix for prefix in prefixes):
        raise ValueError("provide at least one nonempty relevant announcement title prefix")
    return prefixes


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(MAX_ECONOMIC_ICS_BYTES + 1)


def parse_economic_ics(raw: bytes) -> tuple[Any, str]:
    if len(raw) > MAX_ECONOMIC_ICS_BYTES:
        raise ValueError("economic iCalendar capture exceeds 8 MiB")
    try:
        parser = import_module("icalendar")
    except ModuleNotFoundError as exc:
        raise ValueError("iCalendar support requires the aurum-research[calendar] extra") from exc
    try:
        document = parser.Calendar.from_ical(raw)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid economic iCalendar document") from exc
    if document.name != "VCALENDAR":
        raise ValueError("economic iCalendar document must contain one VCALENDAR")

    source_version = (
        f"ical-v{ECONOMIC_ICS_VERSION}/icalendar-{version('icalendar')}"
        f"/dateutil-{version('python-dateutil')}/tzdata-{version('tzdata')}/{sha256(raw).hexdigest()}"
    )
    return document, source_version


def _selected_events(
    document: Any, prefixes: tuple[str, ...], known: dict[str, Announcement]
) -> list[tuple[str, str, Any]]:
    entries = []
    uid_counts: Counter[str] = Counter()
    for component in document.walk("VEVENT"):
        title, uid = component.get("SUMMARY"), component.get("UID")
        titles = title if isinstance(title, list) else [title]
        uids = uid if isinstance(uid, list) else [uid]
        uid_counts.update(item for item in uids if isinstance(item, str))
        selected = any(
            isinstance(item, str) and item.casefold().startswith(prefixes) for item in titles
        )
        tracked = any(isinstance(item, str) and item in known for item in uids)
        if not selected and not tracked:
            continue
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError("selected iCalendar events require unique nonempty UIDs")
        if not isinstance(title, str) or not title.strip():
            if title is not None or uid not in known or not _cancelled(component):
                raise ValueError("selected economic event requires one nonempty SUMMARY")
            title = known[uid].title
        entries.append((uid, title, component))

    if any(uid_counts[uid] != 1 for uid, _, _ in entries):
        raise ValueError("selected iCalendar events require unique nonempty UIDs")
    return entries


def _cancelled(component: Any) -> bool:
    status = component.get("STATUS", "")
    if not isinstance(status, str):
        raise ValueError("selected economic event requires at most one STATUS")
    return status.upper() == "CANCELLED"


def _release_time(component: Any) -> datetime:
    if _RECURRING.intersection(component.keys()):
        raise ValueError("selected economic events must have individual release times")
    try:
        start = component.decoded("DTSTART")
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("selected economic event has an invalid DTSTART") from exc
    if not isinstance(start, datetime) or start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("selected economic event requires a timezone-aware release time")
    wall_time = start.replace(tzinfo=None)
    scheduled_at = utc_from_wall_time(wall_time, start.tzinfo)
    timezone_id = component["DTSTART"].params.get("TZID")
    if isinstance(timezone_id, str):
        try:
            pinned = source_timezone(timezone_id)
        except ValueError:
            pass  # Custom VTIMEZONE definitions travel with the captured file.
        else:
            if utc_from_wall_time(wall_time, pinned) != scheduled_at:
                raise ValueError("economic event offset disagrees with pinned timezone")
    return scheduled_at
