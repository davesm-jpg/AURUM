import json
import sqlite3
import subprocess
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from sys import executable
from zoneinfo import ZoneInfo

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.instruments import MGC, MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp

HEADER = "timestamp,contract,open,high,low,close,volume\n"
CONTRACT = MgcContract("MGC-2025-04")
CONFIG = ImportConfig("synthetic test", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC)


def row(
    timestamp: str, *, contract: str = CONTRACT.code, values: str = "2900,2900.20,2899.90,2900.10,2"
) -> str:
    return f"{timestamp},{contract},{values}\n"


def window(start: str, end: str) -> dict[str, str]:
    return {"start": f"2025-03-10T{start}Z", "end": f"2025-03-10T{end}Z"}


def schedule(*windows: dict[str, str], coverage: dict[str, str] | None = None) -> str:
    return json.dumps(
        {
            "instrument": "MGC",
            "source": "synthetic schedule",
            "version": "fixture-v1",
            "coverage": coverage or window("13:00", "13:02"),
            "windows": windows or [window("13:00", "13:02")],
        }
    )


def csv_file(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "fixture.csv"
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return path


def test_exchange_contract_economics() -> None:
    assert MGC.contract_ounces == 10
    assert MGC.price_ticks("2900.10") == 29001
    assert MGC.tick_value == 1


@pytest.mark.parametrize("code", ["MGC", "MGC-2025-13", "XAUUSD"])
def test_requires_a_dated_mgc_contract(code: str) -> None:
    with pytest.raises(ValueError, match="dated MGC"):
        MgcContract(code)


def test_store_replay_preserves_source_identity_and_close_availability(tmp_path: Path) -> None:
    path = csv_file(
        tmp_path,
        row("2025-03-10T13:01Z", values="2900,2900.2,2899.9,2900.1,0"),
        row("2025-03-10T13:00Z"),
    )
    raw = path.read_bytes()
    calendar = SessionCalendar.from_json(schedule())
    prepared = prepare_csv(path, CONFIG, calendar)
    db_path = tmp_path / "research #?.sqlite"
    store = DatasetStore(db_path)

    with ThreadPoolExecutor(2) as executor:
        ids = list(executor.map(lambda _: DatasetStore(db_path).save(prepared), range(2)))
    assert ids == [prepared.dataset_id, prepared.dataset_id]
    dataset_id = ids[0]
    assert store.source_bytes(dataset_id) == raw
    first = store.describe(dataset_id)
    assert first["provenance"]["source_sha256"] == sha256(raw).hexdigest()
    assert first["provenance"]["data_kind"] == "synthetic"
    assert first["provenance"]["calendar"] == calendar.as_dict()
    assert first["candle_count"] == 2
    assert first["replayable"] is True
    assert [(issue["code"], issue["count"]) for issue in first["issues"]] == [
        ("out_of_order", 1),
        ("zero_volume", 1),
    ]
    assert store.save(prepare_csv(path, CONFIG, calendar)) == dataset_id
    assert store.describe(dataset_id) == first

    replayed = list(DatasetStore(db_path).replay(dataset_id))
    assert replayed == list(prepared.candles)
    assert [bar.available_at for bar in replayed] == [
        datetime(2025, 3, 10, 13, 1, tzinfo=UTC),
        datetime(2025, 3, 10, 13, 2, tzinfo=UTC),
    ]

    changed = SessionCalendar.from_json(schedule().replace("fixture-v1", "fixture-v2"))
    alternate = store.save(prepare_csv(path, CONFIG, changed))
    assert alternate != dataset_id
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM source_blobs").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM datasets").fetchone() == (2,)


def test_gaps_include_edges_and_empty_windows_but_exclude_closures(tmp_path: Path) -> None:
    calendar = SessionCalendar.from_json(
        schedule(
            window("13:00", "13:04"),
            window("13:05", "13:06"),
            window("14:00", "14:03"),
            coverage=window("13:00", "14:03"),
        )
    )
    path = csv_file(
        tmp_path, row("2025-03-10T13:01Z"), row("2025-03-10T13:03Z"), row("2025-03-10T14:00Z")
    )
    prepared = prepare_csv(path, CONFIG, calendar)
    assert [(issue.code, issue.count, issue.start, issue.end) for issue in prepared.issues] == [
        ("missing_candles", 1, "2025-03-10T13:00:00+00:00", "2025-03-10T13:01:00+00:00"),
        ("missing_candles", 1, "2025-03-10T13:02:00+00:00", "2025-03-10T13:03:00+00:00"),
        ("missing_candles", 1, "2025-03-10T13:05:00+00:00", "2025-03-10T13:06:00+00:00"),
        ("missing_candles", 2, "2025-03-10T14:01:00+00:00", "2025-03-10T14:03:00+00:00"),
    ]
    store = DatasetStore(tmp_path / "research.sqlite")
    dataset_id = store.save(prepared)
    assert store.describe(dataset_id)["replayable"] is False
    with pytest.raises(ValueError, match="replay blocked"):
        next(store.replay(dataset_id))


@pytest.mark.parametrize("bounded", (False, True))
def test_paused_public_replay_releases_reader_and_preserves_remaining_candles(
    tmp_path: Path, bounded: bool
) -> None:
    start = minute_timestamp("2025-03-10T00:00Z")
    end = start + 1006 * MINUTE
    calendar = SessionCalendar.from_json(
        schedule(
            {"start": start.isoformat(), "end": end.isoformat()},
            coverage={"start": start.isoformat(), "end": end.isoformat()},
        )
    )
    prepared = prepare_csv(
        csv_file(tmp_path, *(row((start + index * MINUTE).isoformat()) for index in range(1006))),
        CONFIG,
        calendar,
    )
    store = DatasetStore(tmp_path / "synthetic-paused-replay.sqlite")
    dataset = store.save(prepared)
    span = TradingWindow(start + 2 * MINUTE, end - 2 * MINUTE) if bounded else None
    expected = list(prepared.candles[2:-2] if bounded else prepared.candles)
    iterator = store.replay(dataset, span=span)
    assert isinstance(iterator, Generator)
    try:
        for offset in (0, 1000):
            assert next(iterator) == expected[offset]
            # A genuine write transaction must commit while the caller holds a
            # partly consumed replay page. The short timeout makes the former
            # connection-across-yield regression fail promptly.
            with store.connection(write=True) as db:
                db.execute("PRAGMA busy_timeout = 100")
                db.execute("UPDATE datasets SET imported_at = imported_at WHERE id = ?", (dataset,))
            if offset == 0:
                assert [next(iterator) for _ in range(999)] == expected[1:1000]
        assert list(iterator) == expected[1001:]
    finally:
        iterator.close()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([row("2025-03-10T13:00Z"), row("2025-03-10T13:00:00+00:00")], "duplicate candle"),
        ([row("2025-03-10T13:00Z", contract="MGC-2025-06")], "expected contract"),
        ([row("2025-03-10T13:00Z", values="2900,2899,2898,2900,3")], "invalid OHLC"),
        ([row("2025-03-10T13:00Z", values="2900.01,2901,2898,2900,3")], "multiple"),
        ([row("2025-03-10T13:00Z", values="0,2901,2898,2900,3")], "positive"),
        ([row("2025-03-10T13:00Z", values="NaN,2901,2898,2900,3")], "finite"),
        ([row("2025-03-10T13:00Z", values="2900,2901,2898,2900,-3")], "volume"),
        ([row("2025-03-10T13:00Z", values="2900,2901,2898,2900,9223372036854775808")], "volume"),
        ([row("2025-03-10T13:02Z")], "outside open calendar windows"),
        ([row("2025-03-10T13:00:30Z")], "whole UTC minute"),
        ([row("2025-03-10T13:00:00.0000001Z")], "whole UTC minute"),
    ],
)
def test_invalid_rows_are_rejected(tmp_path: Path, rows: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=f"CSV line [23]: .*{message}"):
        prepare_csv(csv_file(tmp_path, *rows), CONFIG, SessionCalendar.from_json(schedule()))


def test_rejects_overlapping_trading_windows() -> None:
    with pytest.raises(ValueError, match="ordered, disjoint"):
        SessionCalendar.from_json(schedule(window("13:00", "13:02"), window("13:01", "13:02")))


def test_rejects_duplicate_schedule_keys() -> None:
    with pytest.raises(ValueError, match="duplicate calendar field: end"):
        SessionCalendar.from_json(
            schedule().replace('"end":', '"end": "2025-01-01T00:00Z", "end":')
        )


def test_close_labeled_rows_at_session_end_and_us_europe_dst_difference(tmp_path: Path) -> None:
    calendar = SessionCalendar.from_json(schedule())
    path = csv_file(tmp_path, row("2025-03-10T08:01-05:00"), row("2025-03-10T08:02"))
    config = ImportConfig(
        "synthetic close-labeled bars",
        CONTRACT,
        "America/Chicago",
        TimestampLabel.CLOSE,
        DataKind.SYNTHETIC,
    )
    bars = prepare_csv(path, config, calendar).candles
    assert bars[0].opened_at == calendar.windows[0].start
    assert bars[1].available_at == calendar.windows[0].end
    assert bars[0].opened_at.astimezone(ZoneInfo("America/New_York")).hour == 9
    assert bars[0].opened_at.astimezone(ZoneInfo("Europe/Prague")).hour == 14


@pytest.mark.parametrize("stamp", ["2025-11-02T01:30", "2025-03-09T02:30"])
def test_ambiguous_or_nonexistent_local_time_requires_disambiguation(stamp: str) -> None:
    with pytest.raises(ValueError, match="ambiguous or nonexistent"):
        minute_timestamp(stamp, ZoneInfo("America/Chicago"))


def test_explicit_dst_fold_offsets_and_offset_mismatch() -> None:
    zone = ZoneInfo("America/Chicago")
    assert minute_timestamp("2025-11-02T01:30-05:00", zone).hour == 6
    assert minute_timestamp("2025-11-02T01:30-06:00", zone).hour == 7
    with pytest.raises(ValueError, match="disagrees"):
        minute_timestamp("2025-03-10T08:00-06:00", zone)


def test_failed_write_rolls_back_provenance_and_raw_bytes(tmp_path: Path) -> None:
    path = csv_file(tmp_path, row("2025-03-10T13:00Z"), row("2025-03-10T13:01Z"))
    calendar = SessionCalendar.from_json(schedule())
    db_path = tmp_path / "research.sqlite"
    store = DatasetStore(db_path)
    store.save(prepare_csv(path, CONFIG, calendar))
    path = csv_file(
        tmp_path,
        row("2025-03-10T13:00Z"),
        row("2025-03-10T13:01Z", values="2900,2900.2,2899.9,2900.1,999"),
    )
    pending = prepare_csv(path, CONFIG, calendar)
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TRIGGER fail_fixture BEFORE INSERT ON candles WHEN new.volume = 999
                   BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated write failure"):
        store.save(pending)
    with pytest.raises(ValueError, match="unknown dataset"):
        store.describe(pending.dataset_id)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM source_blobs").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM candles").fetchone() == (2,)


def test_dated_import_uses_coverage_without_losing_full_session_anchors(tmp_path: Path) -> None:
    snapshot = json.loads(schedule(coverage=window("13:01", "13:04")))
    snapshot.pop("windows")
    snapshot.update(
        schema_version=2,
        sessions=[
            {
                "trade_date": "2025-03-10",
                "windows": [
                    {"start": "2025-03-09T17:00-05:00", "end": "2025-03-10T13:02Z"},
                    window("13:03", "16:30"),
                ],
            }
        ],
    )
    calendar = SessionCalendar.from_dict(snapshot)
    assert calendar.sessions is not None
    session = calendar.sessions[0]
    assert session.start == datetime(2025, 3, 9, 22, tzinfo=UTC)
    assert session.end == datetime(2025, 3, 10, 16, 30, tzinfo=UTC)
    assert calendar.windows[0].start == calendar.coverage.start
    assert calendar.windows[-1].end == calendar.coverage.end

    path = csv_file(tmp_path, row("2025-03-10T13:01Z"), row("2025-03-10T13:03Z"))
    dataset = prepare_csv(path, CONFIG, calendar)
    assert dataset.issues == ()
    restored = SessionCalendar.from_dict(json.loads(dataset.provenance_json)["calendar"])
    assert restored == calendar
    assert prepare_csv(path, CONFIG, restored).dataset_id == dataset.dataset_id
    snapshot["sessions"][0]["trade_date"] = "2025-03-11"
    reassigned = prepare_csv(path, CONFIG, SessionCalendar.from_dict(snapshot))
    assert reassigned.dataset_id != dataset.dataset_id

    path = csv_file(tmp_path, row("2025-03-10T13:01Z"))
    incomplete = prepare_csv(path, CONFIG, calendar)
    assert [(issue.code, issue.count, issue.start) for issue in incomplete.issues] == [
        ("missing_candles", 1, "2025-03-10T13:03:00+00:00")
    ]


@pytest.mark.parametrize("dated", [False, True])
def test_cli_import_inspect_and_replay(tmp_path: Path, dated: bool) -> None:
    path = csv_file(tmp_path, row("2025-03-10T13:00Z"), row("2025-03-10T13:01Z"))
    calendar = tmp_path / "schedule.json"
    snapshot = json.loads(schedule())
    if dated:
        snapshot.update(
            schema_version=2,
            sessions=[{"trade_date": "2025-03-10", "windows": snapshot.pop("windows")}],
        )
    calendar.write_text(json.dumps(snapshot), encoding="utf-8")
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(tmp_path / "test.sqlite")]
    imported = subprocess.run(
        [
            *aurum,
            "import-csv",
            str(path),
            "--calendar",
            str(calendar),
            "--provider",
            "test",
            "--contract",
            CONTRACT.code,
            "--source-timezone",
            "UTC",
            "--timestamp-label",
            "open",
            "--data-kind",
            "synthetic",
            "--price-basis",
            "unadjusted",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    dataset_id = json.loads(imported.stdout)["dataset_id"]
    inspected = subprocess.run(
        [*aurum, "inspect", dataset_id], capture_output=True, text=True, check=True
    )
    assert imported.stdout == inspected.stdout
    replayed = subprocess.run(
        [*aurum, "replay", dataset_id], capture_output=True, text=True, check=True
    )
    events = [json.loads(line) for line in replayed.stdout.splitlines()]
    assert len(events) == 2
    assert events[0]["event"] == "candle_closed"
    assert events[0]["event_id"] == f"{dataset_id}/2025-03-10T13:00:00+00:00"
    assert events[0]["data_kind"] == "synthetic"
    assert events[0]["available_at"] == "2025-03-10T13:01:00+00:00"
    assert events[0]["contract"] == CONTRACT.code
    assert events[1]["available_at"] == "2025-03-10T13:02:00+00:00"
    if dated:
        assert [event["trade_date"] for event in events] == ["2025-03-10", "2025-03-10"]
    else:
        assert all("trade_date" not in event for event in events)
        assert dataset_id == "be2863eb8f8d69c29ace0411e0f37628694e05427e92bd611bd691ef821147bd"
