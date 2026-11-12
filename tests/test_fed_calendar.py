"""Invented monthly HTML and HTTP fixtures, never historical market evidence."""

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from sys import executable
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from aurum import economic_archive
from aurum.economic_archive import (
    EconomicPublisher,
    capture_official_calendar,
    economic_capture_bytes,
    economic_receipts,
    normalize_archived_calendar,
)
from aurum.economic_events import EconomicCalendar, EconomicSourceKind, EventRisk
from aurum.economic_polling import (
    EconomicPollConfig,
    poll_economic_calendar,
    register_economic_poller,
)
from aurum.exchange_calendar import TradingWindow
from aurum.fed_calendar import (
    MAX_FED_HTML_BYTES,
    fed_month_url,
    fed_source_month,
    normalize_fed_monthly,
    parse_fed_monthly,
)
from aurum.providers import NoRedirect
from aurum.storage import DatasetStore
from aurum.storage_admin import backup_store, verify_store
from aurum.timestamps import minute_timestamp, source_timezone

MARCH = "https://www.federalreserve.gov/newsevents/2025-march.htm"
NOVEMBER = "https://www.federalreserve.gov/newsevents/2025-november.htm"
OBSERVED = minute_timestamp("2025-03-01T15:00Z")


def panel(day: str = "10", clock: str = "2:00 p.m.", title: str = "FOMC Minutes") -> str:
    return (
        '<div class="panel-body"><div class="row">'
        f'<div class="col-xs-2"><p>{clock}</p></div>'
        f'<div class="col-xs-7"><p>{title}</p>'
        "<p><p>Meeting of February 27-28 (synthetic description)</p></p></div>"
        f'<div class="col-xs-3"><p>{day}</p></div></div></div>'
    )


def monthly(rows: str | None = None, month: str = "March 2025") -> bytes:
    return (
        '<html><div id="article" class="cal-nojs clearfix">'
        f'<div class="row-title"><h4>{month}</h4></div>'
        f"<h4>Speeches</h4>{panel('1', 'TBA', 'Synthetic speech')}"
        f"<h4>FOMC Meetings </h4>{panel() if rows is None else rows}"
        f"<h4>Beige Book</h4>{panel('20', 'TBA', 'Synthetic book')}"
        "</div></html>"
    ).encode()


def normalize(
    raw: bytes,
    *,
    observed_at: datetime = OBSERVED,
    coverage: TradingWindow | None = None,
    title_prefixes: tuple[str, ...] = ("FOMC",),
) -> EconomicCalendar:
    return normalize_fed_monthly(
        raw,
        source_url=MARCH,
        capture_id="a" * 64,
        observed_at=observed_at,
        coverage=coverage or parse_fed_monthly(monthly(), source_url=MARCH).coverage,
        title_prefixes=title_prefixes,
        data_kind=EconomicSourceKind.SYNTHETIC,
    )


def test_release_column_not_description_and_cross_atlantic_dst() -> None:
    raw = monthly(panel("10") + panel("31", "2:30 p.m.", "FOMC Press Conference"))
    parsed = parse_fed_monthly(raw, source_url=MARCH)
    assert parsed.events == (
        ("FOMC Minutes", minute_timestamp("2025-03-10T18:00Z")),
        ("FOMC Press Conference", minute_timestamp("2025-03-31T18:30Z")),
    )
    prague = source_timezone("Europe/Prague")
    assert [stamp.astimezone(prague).hour for _, stamp in parsed.events] == [19, 20]
    assert parsed.coverage == TradingWindow(
        minute_timestamp("2025-03-01T05:00Z"), minute_timestamp("2025-04-01T04:00Z")
    )
    winter = parse_fed_monthly(monthly(panel("18"), "November 2025"), source_url=NOVEMBER)
    assert winter.events[0][1] == minute_timestamp("2025-11-18T19:00Z")


@pytest.mark.parametrize(
    ("stamp", "suffix"),
    [
        ("2025-04-01T03:59Z", "2025-march.htm"),
        ("2025-04-01T04:00Z", "2025-april.htm"),
        ("2026-01-01T04:59Z", "2025-december.htm"),
        ("2026-01-01T05:00Z", "2026-january.htm"),
    ],
)
def test_month_request_uses_new_york_clock(stamp: str, suffix: str) -> None:
    assert fed_month_url(minute_timestamp(stamp)).endswith(suffix)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.federalreserve.gov/newsevents/calendar.htm",
        MARCH + "?redirect=1",
        MARCH + "#fragment",
        MARCH.replace("https:", "http:"),
        MARCH.replace("www.federalreserve.gov", "www.federalreserve.gov:443"),
        MARCH.replace("2025-march", "0000-march"),
        MARCH.replace("2025-march", "2025-invalid"),
        MARCH.replace("www.federalreserve.gov", "fake.example"),
    ],
)
def test_fixed_official_monthly_source_only(url: str) -> None:
    with pytest.raises(ValueError):
        fed_source_month(url)
    assert not EconomicPublisher.FED.accepts_source_url(url)


def test_observed_publication_gate_and_partial_risk() -> None:
    calendar = normalize(monthly())
    release = calendar.events[0]
    assert release.published_at == OBSERVED and release.actual_at is None
    assert calendar.data_kind is EconomicSourceKind.SYNTHETIC
    assert calendar.assess(OBSERVED - timedelta(minutes=1), 20).risk is EventRisk.UNKNOWN
    assert calendar.assess(release.scheduled_at, 20).risk is EventRisk.ELEVATED
    assert calendar.assess(release.scheduled_at + timedelta(hours=2), 20).risk is EventRisk.UNKNOWN
    assert not calendar.coverage_complete
    filtered = normalize(monthly(), title_prefixes=("FOMC Press Conference",))
    assert filtered.events == () and not filtered.coverage_complete
    assert filtered.version != calendar.version
    later = normalize(monthly(), observed_at=release.scheduled_at + timedelta(minutes=1))
    assert later.assess(release.scheduled_at, 20).risk is EventRisk.UNKNOWN
    assert EconomicCalendar.from_json(json.dumps(calendar.as_dict())) == calendar


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (monthly(panel(clock="TBA")), "explicit supported release time"),
        (monthly(panel(clock="13:00 p.m.")), "explicit supported release time"),
        (monthly(panel(clock="2 p.m.")), "explicit supported release time"),
        (monthly(panel("10-11")), "explicit release dates"),
        (monthly(panel("32")), "day is out of range"),
        (monthly(panel() + panel()), "duplicated"),
        (monthly(panel(title="Synthetic meeting")), "unsupported release title"),
        (
            monthly().replace(b"col-xs-2", b"col-xs-4", 1).replace(b"col-xs-2", b"col-xs-4"),
            "columns",
        ),
        (monthly().replace(b"FOMC Meetings", b"Unknown section"), "no recognized FOMC"),
        (monthly().replace(b"<h4>Beige Book</h4>", b"<h4>FOMC Meetings</h4>"), "repeats it"),
        (monthly(month="April 2025"), "title disagrees"),
        (monthly().replace(b'id="article"', b'id="article" id="other"'), "ambiguous structural"),
        (b"<html>temporary outage</html>", "layout"),
        (b"\xff", "UTF-8"),
        (b"", "size"),
        (b"x" * (MAX_FED_HTML_BYTES + 1), "size"),
        (b"<div>" * 130, "structural limits"),
    ],
)
def test_changed_or_ambiguous_layout_fails_closed(raw: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_fed_monthly(raw, source_url=MARCH)


@pytest.mark.parametrize(
    ("raw", "url"),
    [
        (monthly(panel("9", "2:30 a.m.")), MARCH),
        (monthly(panel("2", "1:30 a.m."), "November 2025"), NOVEMBER),
    ],
)
def test_nonexistent_or_ambiguous_local_release_times_rejected(raw: bytes, url: str) -> None:
    with pytest.raises(ValueError, match="ambiguous|nonexistent"):
        parse_fed_monthly(raw, source_url=url)


def test_explicit_empty_section_and_midnight_noon() -> None:
    assert parse_fed_monthly(monthly(""), source_url=MARCH).events == ()
    parsed = parse_fed_monthly(
        monthly(panel("10, 11", "12:00 a.m.") + panel("12", "12:00 p.m.")), source_url=MARCH
    )
    assert [stamp.hour for _, stamp in parsed.events] == [4, 4, 16]
    with pytest.raises(ValueError, match="within the captured source month"):
        normalize(
            monthly(),
            coverage=TradingWindow(OBSERVED - timedelta(days=2), OBSERVED + timedelta(days=2)),
        )


@dataclass
class FakeClock:
    at: datetime = OBSERVED

    def __call__(self) -> datetime:
        return self.at


@dataclass
class FakeResponse:
    raw: bytes
    length: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Length": str(len(self.raw)) if self.length is None else self.length}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self, limit: int) -> bytes:
        assert limit == MAX_FED_HTML_BYTES + 1
        return self.raw[:limit]


def route(monkeypatch: pytest.MonkeyPatch, url: str, response: FakeResponse | HTTPError) -> None:
    class Opener:
        def open(self, request: Request, *, timeout: int) -> FakeResponse:
            assert request.full_url == url and request.get_method() == "GET"
            assert request.get_header("Accept") == "text/html"
            assert request.get_header("Authorization") is None and timeout == 20
            if isinstance(response, HTTPError):
                raise response
            return response

    def build(redirect: type[NoRedirect]) -> Opener:
        assert redirect is NoRedirect
        return Opener()

    monkeypatch.setattr(economic_archive, "build_opener", build)


def test_monthly_capture_normalization_cli_and_verified_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-fed.sqlite")
    raw = monthly()
    route(monkeypatch, MARCH, FakeResponse(raw))
    receipt = capture_official_calendar(store, EconomicPublisher.FED, clock=FakeClock())
    assert capture_official_calendar(store, EconomicPublisher.FED, clock=FakeClock()) == receipt
    assert economic_capture_bytes(store, receipt) == raw
    assert receipt.as_dict()["source_format"] == "fed_monthly_html"
    calendar = normalize_archived_calendar(
        store,
        (receipt.capture_id,),
        coverage=parse_fed_monthly(raw, source_url=MARCH).coverage,
        title_prefixes=("FOMC",),
    )
    assert calendar.data_kind is EconomicSourceKind.OBSERVED_PUBLICATION
    assert calendar.events[0].published_at == OBSERVED
    cli = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    result = subprocess.run(
        [
            *cli,
            "normalize-economic-captures",
            receipt.capture_id,
            "--start",
            calendar.coverage.start.isoformat(),
            "--end",
            calendar.coverage.end.isoformat(),
            "--title-prefix",
            "FOMC",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert EconomicCalendar.from_json(result.stdout) == calendar
    audit = verify_store(store, verify_normalized=True)["normalized"]
    assert isinstance(audit, dict) and audit["economic_captures_verified"] == 1
    backup_store(store, tmp_path / "synthetic-backup.sqlite", verify_normalized=True)
    later = capture_official_calendar(
        store, EconomicPublisher.FED, clock=FakeClock(OBSERVED + timedelta(minutes=1))
    )
    with pytest.raises(ValueError, match="individual review"):
        normalize_archived_calendar(
            store,
            (receipt.capture_id, later.capture_id),
            coverage=calendar.coverage,
            title_prefixes=("FOMC",),
        )


def test_durable_poller_rotates_month_without_automatic_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-monthly-poller.sqlite")
    clock = FakeClock(minute_timestamp("2025-04-01T03:59Z"))
    config = EconomicPollConfig(EconomicPublisher.FED, interval_seconds=300)
    reference = register_economic_poller(store, config, now=clock())
    route(monkeypatch, MARCH, FakeResponse(monthly()))
    first = poll_economic_calendar(store, reference, clock=clock)
    assert first["state"] == "captured" and first["automatic_monitor_adoption"] is False
    clock.at += timedelta(minutes=5)
    april = fed_month_url(clock())
    route(monkeypatch, april, FakeResponse(monthly(month="April 2025")))
    second = poll_economic_calendar(store, reference, clock=clock)
    assert second["last_capture_id"] != first["last_capture_id"]
    assert register_economic_poller(store, config, now=clock()) == reference
    clock.at += timedelta(minutes=5)
    route(monkeypatch, april, HTTPError(april, 503, "private text", Message(), None))
    failed = poll_economic_calendar(store, reference, clock=clock)
    assert failed["last_capture_id"] == second["last_capture_id"]
    attempt = failed["latest_attempt"]
    assert isinstance(attempt, dict) and attempt["error_code"] == "http_503"
    assert "private text" not in json.dumps(failed)
    assert len(economic_receipts(store)) == 2
    audit = verify_store(store, verify_normalized=True)["normalized"]
    assert isinstance(audit, dict)
    assert audit["economic_pollers_verified"] == 1
    assert audit["economic_poll_attempts_verified"] == 3


@pytest.mark.parametrize("raw", [b"outage", monthly(month="April 2025")])
def test_invalid_http_capture_does_not_archive(
    raw: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-invalid-fed.sqlite")
    route(monkeypatch, MARCH, FakeResponse(raw))
    with pytest.raises(ValueError):
        capture_official_calendar(store, EconomicPublisher.FED, clock=FakeClock())
    assert not store.path.exists()


def test_backward_receipt_clock_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-fed-clock.sqlite")
    route(monkeypatch, MARCH, FakeResponse(monthly()))
    ticks = iter((OBSERVED, OBSERVED - timedelta(seconds=1)))
    with pytest.raises(ValueError, match="precedes its request"):
        capture_official_calendar(store, EconomicPublisher.FED, clock=lambda: next(ticks))
    assert not store.path.exists()


def test_request_crossing_month_boundary_keeps_requested_month(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-crossing-fed.sqlite")
    before = datetime.fromisoformat("2025-04-01T03:59:59+00:00")
    after = before + timedelta(seconds=2)
    reference = register_economic_poller(
        store, EconomicPollConfig(EconomicPublisher.FED), now=before
    )
    route(monkeypatch, MARCH, FakeResponse(monthly()))
    ticks = iter((before, before, after, after))
    result = poll_economic_calendar(store, reference, clock=lambda: next(ticks))
    assert result["state"] == "captured"
    receipt = economic_receipts(store)[0]
    assert receipt.source_url == MARCH and receipt.observed_at == after
    audit = verify_store(store, verify_normalized=True)["normalized"]
    assert isinstance(audit, dict) and audit["economic_poll_attempts_verified"] == 1


def test_response_size_mismatch_is_not_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-fed-size.sqlite")
    route(monkeypatch, MARCH, FakeResponse(monthly(), length="999999"))
    with pytest.raises(ValueError, match="Content-Length"):
        capture_official_calendar(store, EconomicPublisher.FED, clock=FakeClock())
    assert not store.path.exists()
