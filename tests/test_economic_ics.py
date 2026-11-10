import json
import subprocess
from datetime import date
from hashlib import sha256
from pathlib import Path
from sys import executable

import pytest

from aurum.economic_events import EconomicCalendar, EconomicSourceKind, EventRisk
from aurum.economic_ics import normalize_economic_ics
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
