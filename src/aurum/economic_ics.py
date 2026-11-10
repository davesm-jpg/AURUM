"""Normalize a reviewed local iCalendar capture into causal scheduled-event evidence."""

from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

from aurum.economic_events import Announcement, EconomicCalendar, EconomicSourceKind
from aurum.exchange_calendar import TradingWindow
from aurum.market_data import canonical_json
from aurum.timestamps import source_timezone, utc_from_wall_time

MAX_ECONOMIC_ICS_BYTES = 8 * 1024 * 1024
ECONOMIC_ICS_VERSION = 1
_RECURRING = {"RRULE", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID"}


def normalize_economic_ics(
    path: Path,
    *,
    source: str,
    coverage: TradingWindow,
    observed_at: datetime,
    data_kind: EconomicSourceKind,
    title_prefixes: tuple[str, ...],
) -> EconomicCalendar:
    if observed_at.tzinfo is not UTC:
        raise ValueError("economic calendar capture time must be UTC")
    prefixes = tuple(prefix.strip().casefold() for prefix in title_prefixes)
    if not prefixes or any(not prefix for prefix in prefixes):
        raise ValueError("provide at least one nonempty relevant announcement title prefix")
    with path.open("rb") as stream:
        raw = stream.read(MAX_ECONOMIC_ICS_BYTES + 1)
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

    events = []
    seen: set[str] = set()
    for component in document.walk("VEVENT"):
        title = component.get("SUMMARY")
        if not isinstance(title, str) or not title.casefold().startswith(prefixes):
            continue
        uid = component.get("UID")
        if not isinstance(uid, str) or not uid.strip() or uid in seen:
            raise ValueError("selected iCalendar events require unique nonempty UIDs")
        seen.add(uid)
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
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
        if coverage.start <= scheduled_at < coverage.end:
            event_id = sha256(canonical_json((source, uid)).encode()).hexdigest()
            events.append(Announcement(event_id, title, scheduled_at, observed_at))

    source_version = (
        f"ical-v{ECONOMIC_ICS_VERSION}/icalendar-{version('icalendar')}"
        f"/dateutil-{version('python-dateutil')}/tzdata-{version('tzdata')}/{sha256(raw).hexdigest()}"
    )
    return EconomicCalendar(
        source,
        source_version,
        coverage,
        observed_at,
        tuple(sorted(events, key=lambda event: (event.scheduled_at, event.event_id))),
        coverage_complete=False,
        data_kind=data_kind,
    )
