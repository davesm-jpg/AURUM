import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from hashlib import sha256
from pathlib import Path
from sys import executable
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from aurum import economic_archive
from aurum.economic_archive import (
    EconomicPublisher,
    capture_official_ics,
    economic_capture_bytes,
    economic_receipt,
    economic_receipts,
    normalize_archived_ics,
)
from aurum.economic_events import EconomicCalendar, EventRisk
from aurum.economic_ics import MAX_ECONOMIC_ICS_BYTES
from aurum.exchange_calendar import TradingWindow
from aurum.providers import NoRedirect
from aurum.storage import SCHEMA_VERSION, DatasetStore
from aurum.storage_admin import backup_store, verify_store
from aurum.timestamps import minute_timestamp

pytest.importorskip("icalendar")

EARLY = minute_timestamp("2025-03-10T12:00Z")
LATER = minute_timestamp("2025-03-10T12:05Z")
REVISION = minute_timestamp("2025-03-10T12:10Z")
WINDOW = TradingWindow(EARLY, minute_timestamp("2025-03-10T14:00Z"))


def synthetic_ics(start: str, sequence: int = 0) -> bytes:
    return (
        "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//AURUM//Synthetic HTTP Fixture//EN",
                "BEGIN:VEVENT",
                "UID:synthetic-release",
                "SUMMARY:Synthetic release fixture",
                f"DTSTART:{start}",
                "DTSTAMP:20250308T120000Z",
                f"SEQUENCE:{sequence}",
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        )
        + "\r\n"
    ).encode()


@dataclass
class SyntheticResponse:
    raw: bytes
    length: str | None = None
    finished: bool = False

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Length": self.length if self.length is not None else str(len(self.raw))}

    def __enter__(self) -> "SyntheticResponse":
        return self

    def __exit__(self, *_: object) -> None:
        self.finished = True

    def read(self, limit: int) -> bytes:
        assert limit == MAX_ECONOMIC_ICS_BYTES + 1
        return self.raw[:limit]


@dataclass
class SyntheticOpener:
    publisher: EconomicPublisher
    response: SyntheticResponse | HTTPError

    def open(self, request: Request, *, timeout: int) -> SyntheticResponse:
        assert request.full_url == self.publisher.url
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") is None
        assert timeout == 20
        if isinstance(self.response, HTTPError):
            raise self.response
        return self.response


def route_synthetic(
    monkeypatch: pytest.MonkeyPatch,
    publisher: EconomicPublisher,
    response: SyntheticResponse | HTTPError,
) -> None:
    def opener(redirect: type[NoRedirect]) -> SyntheticOpener:
        assert redirect is NoRedirect
        return SyntheticOpener(publisher, response)

    monkeypatch.setattr(economic_archive, "build_opener", opener)


def test_archive_preserves_receipts_and_bytes_across_schema_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-economic.sqlite")
    with store.connection(write=True) as db:
        db.execute("DROP TABLE economic_captures")
        db.execute("PRAGMA user_version = 13")
    assert economic_receipts(store) == []
    first_body = synthetic_ics("20250310T123000Z")
    revised_body = synthetic_ics("20250310T124500Z", sequence=1)

    def capture(body: bytes, at: datetime) -> str:
        response = SyntheticResponse(body)
        route_synthetic(monkeypatch, EconomicPublisher.BEA, response)

        def after_complete() -> datetime:
            assert response.finished
            return at

        return capture_official_ics(store, EconomicPublisher.BEA, clock=after_complete).capture_id

    first = capture(first_body, EARLY)
    assert capture(first_body, EARLY) == first
    repeated = capture(first_body, LATER)
    changed = capture(revised_body, REVISION)
    assert len({first, repeated, changed}) == 3
    assert [item.capture_id for item in economic_receipts(store)] == [changed, repeated, first]
    with pytest.raises(ValueError, match="conflicting"):
        capture(revised_body, LATER)
    inspected = economic_receipt(store, first)
    assert inspected.observed_at == EARLY
    assert inspected.source_sha256 == sha256(first_body).hexdigest()
    assert inspected.byte_count == len(first_body)
    assert economic_capture_bytes(store, inspected) == first_body
    verification = verify_store(store, verify_sources=True)
    assert verification["schema_version"] == SCHEMA_VERSION == 14
    assert verification["source_checksums_verified"] == 2

    cli = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    raw = subprocess.run([*cli, "economic-capture-raw", first], capture_output=True, check=True)
    assert raw.stdout == first_body
    details = subprocess.run(
        [*cli, "economic-captures", first], capture_output=True, check=True, text=True
    )
    assert json.loads(details.stdout) == inspected.as_dict()
    args = [
        *cli,
        "normalize-economic-captures",
        first,
        repeated,
        changed,
        "--start",
        WINDOW.start.isoformat(),
        "--end",
        WINDOW.end.isoformat(),
        "--title-prefix",
        "Synthetic release",
    ]
    normalized = subprocess.run(args, capture_output=True, check=True, text=True)
    calendar = EconomicCalendar.from_json(normalized.stdout)
    assert not calendar.coverage_complete
    assert [item.observed_at for item in calendar.captures] == [EARLY, LATER, REVISION]
    assert [item.version.split("/")[1] for item in calendar.captures] == [first, repeated, changed]
    original, moved = calendar.events
    assert original.published_at == EARLY
    assert moved.published_at == REVISION and moved.supersedes == original.event_id
    assert calendar.assess(LATER, 30).event_ids == (original.event_id,)
    assert calendar.assess(original.scheduled_at, 0).risk is EventRisk.UNKNOWN
    assert calendar.assess(moved.scheduled_at, 0).event_ids == (moved.event_id,)
    single = normalize_archived_ics(
        store, (changed,), coverage=WINDOW, title_prefixes=("Synthetic release",)
    )
    assert single.version.startswith(f"archive-v1/{changed}/")
    assert single.events[0].published_at == REVISION
    destination = tmp_path / "synthetic-verified.sqlite"
    backed_up = backup_store(store, destination, verify_normalized=True)
    normalized_audit = backed_up["normalized"]
    assert isinstance(normalized_audit, dict)
    assert normalized_audit["economic_captures_verified"] == 3
    assert economic_receipt(DatasetStore(destination), first) == inspected

    with store.connection(write=True) as db:
        db.execute(
            "UPDATE source_blobs SET raw = ? WHERE sha256 = ?",
            (first_body.replace(b"Synthetic", b"Different"), inspected.source_sha256),
        )
    with pytest.raises(ValueError, match="source bytes do not match"):
        economic_capture_bytes(store, inspected)
    with pytest.raises(ValueError, match="preserved source checksum mismatch"):
        verify_store(store, verify_sources=True)
    rejected = tmp_path / "synthetic-rejected.sqlite"
    with pytest.raises(ValueError, match="failed archive audit"):
        backup_store(store, rejected, verify_normalized=True)
    assert not rejected.exists()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (HTTPError(EconomicPublisher.BLS.url, 403, "denied", Message(), None), "HTTP 403"),
        (SyntheticResponse(b"<html>unavailable</html>"), "invalid economic iCalendar"),
        (SyntheticResponse(synthetic_ics("20250310T123000Z"), "99999"), "Content-Length"),
    ],
)
def test_failed_official_capture_creates_no_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: SyntheticResponse | HTTPError,
    message: str,
) -> None:
    store = DatasetStore(tmp_path / "unused-synthetic.sqlite")
    route_synthetic(monkeypatch, EconomicPublisher.BLS, response)
    with pytest.raises(ValueError, match=message):
        capture_official_ics(store, EconomicPublisher.BLS, clock=lambda: EARLY)
    assert not store.path.exists()


def test_receipt_metadata_tampering_and_cross_source_replay_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DatasetStore(tmp_path / "synthetic-economic.sqlite")
    raw = synthetic_ics("20250310T123000Z")
    route_synthetic(monkeypatch, EconomicPublisher.BEA, SyntheticResponse(raw))
    bea = capture_official_ics(store, EconomicPublisher.BEA, clock=lambda: EARLY)
    route_synthetic(monkeypatch, EconomicPublisher.BLS, SyntheticResponse(raw))
    bls = capture_official_ics(store, EconomicPublisher.BLS, clock=lambda: LATER)
    with pytest.raises(ValueError, match="one official publisher"):
        normalize_archived_ics(
            store,
            (bea.capture_id, bls.capture_id),
            coverage=WINDOW,
            title_prefixes=("Synthetic release",),
        )
    with store.connection(write=True) as db:
        db.execute(
            "UPDATE economic_captures SET source_url = 'modified' WHERE id = ?", (bea.capture_id,)
        )
    with pytest.raises(ValueError, match="metadata does not match"):
        economic_receipt(store, bea.capture_id)
    with pytest.raises(ValueError, match="failed archive audit"):
        verify_store(store, verify_normalized=True)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM source_blobs").fetchone() == (1,)


def test_archive_api_previews_local_receipts_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    store = DatasetStore(tmp_path / "synthetic-economic.sqlite")
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(synthetic_ics("20250310T123000Z"))
    )
    receipt = capture_official_ics(store, EconomicPublisher.BEA, clock=lambda: EARLY)
    expected = normalize_archived_ics(
        store, (receipt.capture_id,), coverage=WINDOW, title_prefixes=("Synthetic release",)
    )
    checksum = sha256(store.path.read_bytes()).hexdigest()
    request = {
        "capture_ids": [receipt.capture_id],
        "start": WINDOW.start.isoformat(),
        "end": WINDOW.end.isoformat(),
        "title_prefixes": ["Synthetic release"],
    }
    with http.TestClient(api.create_app(store)) as client:
        assert client.get("/api/economic-captures").json() == [receipt.as_dict()]
        assert (
            client.get(f"/api/economic-captures/{receipt.capture_id}").json() == receipt.as_dict()
        )
        preview = client.post("/api/economic-calendar/preview", json=request)
        assert preview.status_code == 200
        assert preview.json() == expected.as_dict()
        absent = client.post(
            "/api/economic-calendar/preview", json={**request, "capture_ids": ["0" * 64]}
        )
        assert absent.status_code == 404
        extra = client.post(
            "/api/economic-calendar/preview", json={**request, "observed_at": LATER.isoformat()}
        )
        assert extra.status_code == 422
    assert sha256(store.path.read_bytes()).hexdigest() == checksum
