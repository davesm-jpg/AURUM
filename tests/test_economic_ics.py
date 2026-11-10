import json
import subprocess
from datetime import date
from hashlib import sha256
from pathlib import Path
from sys import executable

import pytest

from aurum.economic_events import EconomicCalendar, EconomicSourceKind, EventRisk
from aurum.economic_ics import compare_economic_ics, normalize_economic_ics
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import MarketMonitor, MonitorConfig
from aurum.providers import FeedStatus
from aurum.runtime_profiles import ProviderProfile
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
from aurum.timestamps import minute_timestamp

START = minute_timestamp("2025-03-07T00:00Z")
END = minute_timestamp("2025-03-12T00:00Z")
CAPTURE = minute_timestamp("2025-03-10T12:20Z")
WINDOW = TradingWindow(START, END)


def _event(uid: str, title: str, start: str, *other: str) -> list[str]:
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        "DTSTAMP:20250201T000000Z",
        start,
        f"SUMMARY:{title}",
        *other,
        "END:VEVENT",
    ]


def _capture(path: Path, *events: list[str]) -> Path:
    rows = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AURUM//Synthetic Fixture//EN"]
    for event in events:
        rows.extend(event)
    path.write_bytes(("\r\n".join([*rows, "END:VCALENDAR"]) + "\r\n").encode())
    return path


def test_ical_capture_preserves_observation_time_partial_scope_and_dst(tmp_path: Path) -> None:
    pytest.importorskip("icalendar")
    path = _capture(
        tmp_path / "synthetic.ics",
        _event(
            "synthetic-a", "Synthetic release one", "DTSTART;TZID=America/New_York:20250307T083000"
        ),
        _event(
            "synthetic-b", "Synthetic release two", "DTSTART;TZID=America/New_York:20250310T083000"
        ),
        _event(
            "synthetic-c",
            "Synthetic release canceled",
            "DTSTART:20250310T123500Z",
            "STATUS:CANCELLED",
        ),
        _event("synthetic-holiday", "Other synthetic calendar item", "DTSTART;VALUE=DATE:20250310"),
    )
    store = tmp_path / "unused-synthetic.sqlite"
    args = [
        str(Path(executable).with_name("aurum")),
        "--db",
        str(store),
        "normalize-economic-ics",
        str(path),
        "--source",
        "synthetic calendar publication",
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
        "--observed-at",
        CAPTURE.isoformat(),
        "--title-prefix",
        "synthetic release",
        "--data-kind",
        "synthetic",
    ]
    output = subprocess.run(args, check=True, capture_output=True, text=True)
    payload = json.loads(output.stdout)
    assert payload["data_kind"] == "synthetic"
    assert payload["coverage_complete"] is False
    assert payload["version"].endswith(sha256(path.read_bytes()).hexdigest())
    assert not store.exists()
    snapshot = EconomicCalendar.from_json(output.stdout)
    early, later = snapshot.events
    assert early.scheduled_at.isoformat() == "2025-03-07T13:30:00+00:00"
    assert later.scheduled_at.isoformat() == "2025-03-10T12:30:00+00:00"
    assert early.published_at == later.published_at == CAPTURE
    assert snapshot.assess(early.scheduled_at, 5).risk is EventRisk.UNKNOWN
    assert snapshot.assess(minute_timestamp("2025-03-10T12:19Z"), 20).risk is EventRisk.UNKNOWN
    assert snapshot.assess(CAPTURE, 15).event_ids == (later.event_id,)
    assert snapshot.assess(minute_timestamp("2025-03-11T12:00Z"), 15).risk is EventRisk.UNKNOWN
    repeated = subprocess.run(args, check=True, capture_output=True, text=True)
    assert repeated.stdout == output.stdout

    profile = ProviderProfile(
        "Synthetic classification fixture",
        "AURUM_SYNTHETIC_TEST_ENDPOINT",
        FeedStatus.DELAYED,
        calendar_dataset_id="0" * 64,
        economic_calendar=payload,
    )
    with pytest.raises(ValueError, match="simulated provider"):
        profile.economic_events()
    review = SessionCalendar(
        "synthetic exchange calendar",
        "fixture-v1",
        WINDOW,
        sessions=(TradingSession(date(2025, 3, 10), (WINDOW,)),),
    )
    with pytest.raises(ValueError, match="simulated monitor"):
        MarketMonitor(
            DatasetStore(store),
            review,
            MgcContract("MGC-2025-04"),
            MonitorConfig("synthetic source fixture", FeedStatus.REAL_TIME),
            DEFAULT_STRUCTURE_CONFIG,
            DEFAULT_STRATEGY_CONFIG,
            economic=snapshot,
        )


@pytest.mark.parametrize(
    ("start", "extra", "message"),
    [
        ("DTSTART;VALUE=DATE:20250310", (), "timezone-aware"),
        ("DTSTART:20250310T083000", (), "timezone-aware"),
        ("DTSTART;TZID=America/New_York:20250309T023000", (), "ambiguous or nonexistent"),
        ("DTSTART:20250310T123000Z", ("RRULE:FREQ=MONTHLY",), "individual release times"),
    ],
)
def test_ical_rejects_unresolved_release_times(
    tmp_path: Path, start: str, extra: tuple[str, ...], message: str
) -> None:
    pytest.importorskip("icalendar")
    path = _capture(
        tmp_path / "synthetic-invalid.ics",
        _event("synthetic-uid", "Synthetic release", start, *extra),
    )
    with pytest.raises(ValueError, match=message):
        normalize_economic_ics(
            path,
            source="synthetic source",
            coverage=WINDOW,
            observed_at=CAPTURE,
            data_kind=EconomicSourceKind.SYNTHETIC,
            title_prefixes=("Synthetic release",),
        )


def test_compare_ical_captures_keeps_first_observation_and_explicit_changes(tmp_path: Path) -> None:
    pytest.importorskip("icalendar")
    first = _capture(
        tmp_path / "synthetic-first.ics",
        _event(
            "synthetic-moved", "Synthetic release moved", "DTSTART:20250310T123000Z", "SEQUENCE:0"
        ),
        _event("synthetic-omitted", "Synthetic release omitted", "DTSTART:20250310T123100Z"),
        _event("synthetic-cancelled", "Synthetic release canceled", "DTSTART:20250310T123200Z"),
    )
    second = _capture(
        tmp_path / "synthetic-second.ics",
        _event(
            "synthetic-moved", "Revised synthetic title", "DTSTART:20250310T124500Z", "SEQUENCE:1"
        ),
        ["BEGIN:VEVENT", "UID:synthetic-cancelled", "STATUS:CANCELLED", "END:VEVENT"],
        _event("synthetic-new", "Synthetic release new", "DTSTART:20250310T124600Z"),
    )
    at_first = minute_timestamp("2025-03-10T12:12Z")
    at_second = minute_timestamp("2025-03-10T12:15Z")
    at_third = minute_timestamp("2025-03-10T12:17Z")
    args = [
        str(Path(executable).with_name("aurum")),
        "--db",
        str(tmp_path / "unused-synthetic.sqlite"),
        "compare-economic-ics",
        "--source",
        "synthetic calendar publication",
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
        "--title-prefix",
        "synthetic release",
        "--data-kind",
        "synthetic",
        "--capture",
        at_first.isoformat(),
        str(first),
        "--capture",
        at_second.isoformat(),
        str(second),
        "--capture",
        at_third.isoformat(),
        str(second),
    ]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    calendar = EconomicCalendar.from_json(result.stdout)
    payload = json.loads(result.stdout)
    assert calendar.as_dict() == payload
    assert calendar.version.startswith("ical-compare-v1/")
    assert calendar.coverage_known_at == at_first
    assert calendar.data_kind is EconomicSourceKind.SYNTHETIC
    assert calendar.coverage_complete is False
    assert len(calendar.events) == 6
    assert tuple(capture.observed_at for capture in calendar.captures) == (
        at_first,
        at_second,
        at_third,
    )
    for path, capture in zip((first, second, second), calendar.captures, strict=True):
        assert capture.version.endswith(sha256(path.read_bytes()).hexdigest())
    known = {event.title: event for event in calendar.events if not event.cancelled}
    old = known["Synthetic release moved"]
    moved = known["Revised synthetic title"]
    omitted = known["Synthetic release omitted"]
    cancelled = next(event for event in calendar.events if event.cancelled)
    assert moved.supersedes == old.event_id
    assert moved.published_at == cancelled.published_at == at_second
    assert cancelled.supersedes == known["Synthetic release canceled"].event_id
    assert calendar.assess(at_first, 35).event_ids == tuple(
        event.event_id for event in calendar.events if event.published_at == at_first
    )
    assert set(calendar.assess(at_second, 35).event_ids) == {
        moved.event_id,
        omitted.event_id,
        known["Synthetic release new"].event_id,
    }
    assert calendar.assess(at_third, 35) == calendar.assess(at_second, 35)
    assert calendar.assess(old.scheduled_at, 0).risk is EventRisk.UNKNOWN
    assert calendar.assess(omitted.scheduled_at, 0).event_ids == (omitted.event_id,)
    assert calendar.assess(cancelled.scheduled_at, 0).risk is EventRisk.UNKNOWN
    assert calendar.assess(moved.scheduled_at, 0).event_ids == (moved.event_id,)
    assert not (tmp_path / "unused-synthetic.sqlite").exists()
    assert subprocess.run(args, check=True, capture_output=True, text=True).stdout == result.stdout

    profile = ProviderProfile(
        "Synthetic comparison fixture",
        "AURUM_SYNTHETIC_TEST_ENDPOINT",
        FeedStatus.SIMULATED,
        calendar_dataset_id="0" * 64,
        economic_calendar=payload,
    )
    assert profile.economic_events() == calendar

    payload["captures"][1]["observed_at"] = at_third.isoformat()
    with pytest.raises(ValueError, match="strictly increasing"):
        EconomicCalendar.from_dict(payload)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            [_event("same", "Synthetic first", "DTSTART:20250310T124000Z", "SEQUENCE:1")],
            "stale or conflicting SEQUENCE",
        ),
        (
            [_event("same", "Synthetic first", "DTSTART:20250310T124000Z", "SEQUENCE:0")],
            "stale or conflicting SEQUENCE",
        ),
        (
            [
                _event("same", "Synthetic first", "DTSTART:20250310T124000Z"),
                _event("same", "Unmatched title", "DTSTART:20250310T125000Z"),
            ],
            "unique nonempty UIDs",
        ),
        (
            [
                _event(
                    "same",
                    "Synthetic first",
                    "DTSTART:20250310T124000Z",
                    "STATUS:CANCELLED",
                    "RECURRENCE-ID:20250310T124000Z",
                )
            ],
            "individual release times",
        ),
    ],
)
def test_compare_ical_rejects_ambiguous_revisions(
    tmp_path: Path, change: list[list[str]], message: str
) -> None:
    pytest.importorskip("icalendar")
    first = _capture(
        tmp_path / "synthetic-first.ics",
        _event("same", "Synthetic first", "DTSTART:20250310T123000Z", "SEQUENCE:1"),
    )
    second = _capture(tmp_path / "synthetic-second.ics", *change)
    with pytest.raises(ValueError, match=message):
        compare_economic_ics(
            ((first, minute_timestamp("2025-03-10T12:10Z")), (second, CAPTURE)),
            source="synthetic calendar publication",
            coverage=WINDOW,
            data_kind=EconomicSourceKind.SYNTHETIC,
            title_prefixes=("Synthetic",),
        )


def test_compare_ical_does_not_link_changed_uids_or_accept_nonchronological_input(
    tmp_path: Path,
) -> None:
    pytest.importorskip("icalendar")
    first = _capture(
        tmp_path / "synthetic-first.ics",
        _event("before", "Synthetic release", "DTSTART:20250310T123000Z"),
    )
    second = _capture(
        tmp_path / "synthetic-second.ics",
        _event("different", "Synthetic release", "DTSTART:20250310T124000Z"),
        _event(
            "unknown", "Synthetic release canceled", "DTSTART:20250310T125000Z", "STATUS:CANCELLED"
        ),
    )
    prior = minute_timestamp("2025-03-10T12:10Z")

    def compare(reverse: bool = False) -> EconomicCalendar:
        return compare_economic_ics(
            ((first, CAPTURE), (second, prior)) if reverse else ((first, prior), (second, CAPTURE)),
            source="synthetic calendar publication",
            coverage=WINDOW,
            data_kind=EconomicSourceKind.SYNTHETIC,
            title_prefixes=("Synthetic",),
        )

    events = compare().events
    assert len(events) == 2
    assert all(event.supersedes is None and not event.cancelled for event in events)
    with pytest.raises(ValueError, match="strictly increasing"):
        compare(reverse=True)
