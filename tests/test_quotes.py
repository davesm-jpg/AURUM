import json
import sqlite3
import subprocess
from datetime import date
from hashlib import sha256
from pathlib import Path
from sys import executable

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind
from aurum.quotes import QuoteImportConfig, import_quote_csv, list_quote_series, quote_at
from aurum.storage import SCHEMA_VERSION, DatasetStore
from aurum.storage_admin import verify_store
from aurum.timestamps import MINUTE, event_timestamp, minute_timestamp

START = minute_timestamp("2025-03-10T13:00Z")
WINDOWS = (
    TradingWindow(START, START + MINUTE),
    TradingWindow(START + 2 * MINUTE, START + 4 * MINUTE),
)
CALENDAR = SessionCalendar(
    "synthetic split quote calendar",
    "fixture-v1",
    TradingWindow(WINDOWS[0].start, WINDOWS[-1].end),
    sessions=(TradingSession(date(2025, 3, 10), WINDOWS),),
)
CONTRACT = MgcContract("MGC-2025-04")
CONFIG = QuoteImportConfig("synthetic quotes", CONTRACT, "Europe/Prague", DataKind.SYNTHETIC)
HEADER = "observed_at,contract,sequence,bid,ask,bid_size,ask_size\n"
ROWS = (
    "2025-03-10T14:00:00.000000001+01:00,MGC-2025-04,2,2900,2900,,5",
    "2025-03-10T14:00:00.000000001+01:00,MGC-2025-04,1,2899.9,2900.1,2,3",
    "2025-03-10T14:00:30+01:00,MGC-2025-04,3,2900,2900.1,4,5",
    "2025-03-10T14:02:10+01:00,MGC-2025-04,4,2900,2900.2,1,5",
)


def _source(path: Path, rows: tuple[str, ...] = ROWS) -> Path:
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_historical_quote_import_and_lookup_are_exact_and_exchange_aware(tmp_path: Path) -> None:
    path = _source(tmp_path / "synthetic-quotes.csv")
    schedule = tmp_path / "synthetic-schedule.json"
    schedule.write_text(json.dumps(CALENDAR.as_dict()), encoding="utf-8")
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    with store.connection(write=True):
        pass
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE book_quotes")
        db.execute("DROP TABLE quote_series")
        db.execute("PRAGMA user_version = 12")
    assert list_quote_series(store) == []
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    imported = subprocess.run(
        [
            *aurum,
            "import-quotes",
            str(path),
            "--calendar",
            str(schedule),
            "--provider",
            CONFIG.provider,
            "--contract",
            CONTRACT.code,
            "--source-timezone",
            CONFIG.source_timezone,
            "--data-kind",
            "synthetic",
            "--price-basis",
            "unadjusted",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    series = json.loads(imported.stdout)
    series_id = series["series_id"]
    assert import_quote_csv(store, path, CONFIG, CALENDAR) == series_id
    assert series["quote_count"] == 4
    assert series["provenance"]["source_sha256"] == sha256(path.read_bytes()).hexdigest()
    assert series["provenance"]["data_kind"] == "synthetic"
    assert series["provenance"]["timestamp_basis"] == "historical_observation"
    assert {issue["code"] for issue in series["issues"]} == {
        "locked_quotes",
        "out_of_order_quotes",
        "quote_size_unavailable",
    }
    verified = verify_store(store, verify_sources=True)
    assert verified["schema_version"] == SCHEMA_VERSION
    assert verified["source_checksums_verified"] == 1

    def lookup(at: str, maximum_age_ms: int = 1) -> dict[str, object]:
        return quote_at(store, series_id, event_timestamp(at), maximum_age_ms)

    exact = lookup("2025-03-10T13:00:00.000000001Z")
    assert exact["status"] == "not_observed" and exact["quote"] is None
    fresh = lookup("2025-03-10T13:00:00.001000001Z")
    assert fresh["status"] == "fresh" and fresh["age_nanoseconds"] == 1_000_000
    observation = fresh["quote"]
    assert isinstance(observation, dict)
    assert observation["sequence"] == 2 and observation["spread_ticks"] == 0
    assert observation["bid_size"] is None
    stale = lookup("2025-03-10T13:00:00.001000002Z")
    assert stale["status"] == "stale" and stale["age_nanoseconds"] == 1_000_001
    assert lookup("2025-03-10T13:01:00Z")["status"] == "market_closed"
    reopened = lookup("2025-03-10T13:02:01Z", maximum_age_ms=86_400_000)
    assert reopened["status"] == "not_observed" and reopened["quote"] is None
    assert lookup("2025-03-10T13:04:00Z")["status"] == "calendar_unavailable"

    output = subprocess.run(
        [*aurum, "quote-at", series_id, "--at", str(fresh["as_of"]), "--maximum-age-ms", "1"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(output.stdout) == fresh
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    with http.TestClient(api.create_app(store)) as client:
        assert client.get("/api/datasets").json() == []
        assert client.get("/api/quote-series").json() == [series]
        route = f"/api/quote-series/{series_id}/as-of"
        response = client.get(route, params={"at": fresh["as_of"], "maximum_age_ms": 1})
        assert response.status_code == 200
        assert response.json() == fresh


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (ROWS[0], "duplicate quote sequence"),
        (ROWS[-1].replace("2900,2900.2", "2900.3,2900.2"), "uncrossed"),
        (ROWS[-1].replace("14:02:10", "14:01:10"), "outside open calendar"),
        (ROWS[-1].replace("MGC-2025-04", "MGC-2025-06"), "expected contract"),
        (ROWS[-1].replace(",1,5", ",-1,5"), "unsigned integers"),
    ],
)
def test_quote_import_rejects_incompatible_or_invalid_observations(
    tmp_path: Path, row: str, error: str
) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    source = _source(tmp_path / "synthetic-invalid.csv", (ROWS[0], row))
    with pytest.raises(ValueError, match=error):
        import_quote_csv(store, source, CONFIG, CALENDAR)
    assert not store.path.exists()
