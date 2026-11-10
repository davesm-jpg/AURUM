import json
import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from sys import executable

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_trade_csv
from aurum.storage import SCHEMA_VERSION, DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp, source_timezone, trade_timestamp

_START = minute_timestamp("2025-03-10T13:00Z")
_SPAN = TradingWindow(_START, _START + 3 * MINUTE)
_CALENDAR = SessionCalendar(
    "synthetic trade print calendar",
    "fixture-v1",
    _SPAN,
    sessions=(TradingSession(date(2025, 3, 10), (_SPAN,)),),
)
_CONTRACT = MgcContract("MGC-2025-04")
_CONFIG = ImportConfig(
    "synthetic trade prints", _CONTRACT, "Europe/Prague", TimestampLabel.OPEN, DataKind.SYNTHETIC
)
_HEADER = "timestamp,contract,sequence,price,size\n"
_FIRST = "2025-03-10T14:00:00.000000001+01:00"
_ROWS = (
    f"{_FIRST},{_CONTRACT.code},2,2900.1,2",
    f"{_FIRST},{_CONTRACT.code},1,2900.0,1",
    f"2025-03-10T14:00:00.000000002+01:00,{_CONTRACT.code},3,2900.2,3",
    f"2025-03-10T14:00:59.999999999+01:00,{_CONTRACT.code},4,2899.9,4",
    f"2025-03-10T14:01:00.000000000+01:00,{_CONTRACT.code},5,2900.3,5",
    f"2025-03-10T14:02:00.000000000+01:00,{_CONTRACT.code},6,2900.4,6",
)


def _write(path: Path, rows: tuple[str, ...]) -> Path:
    path.write_text(_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_trade_import_preserves_exact_sequence_and_derives_minute_bars(tmp_path: Path) -> None:
    path = _write(tmp_path / "synthetic-prints.csv", _ROWS)
    schedule = tmp_path / "synthetic-schedule.json"
    schedule.write_text(json.dumps(_CALENDAR.as_dict()), encoding="utf-8")
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    imported = subprocess.run(
        [
            *aurum,
            "import-trades",
            str(path),
            "--calendar",
            str(schedule),
            "--provider",
            _CONFIG.provider,
            "--contract",
            _CONTRACT.code,
            "--source-timezone",
            _CONFIG.source_timezone,
            "--data-kind",
            "synthetic",
            "--price-basis",
            "unadjusted",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(imported.stdout)
    dataset_id = metadata["dataset_id"]
    assert metadata == store.describe(dataset_id)
    assert store.save(prepare_trade_csv(path, _CONFIG, _CALENDAR)) == dataset_id
    assert store.source_bytes(dataset_id) == path.read_bytes()
    assert metadata["provenance"]["trade_print_count"] == 6
    assert metadata["provenance"]["source_data_type"] == "trade_prints"
    assert metadata["provenance"]["data_kind"] == "synthetic"
    assert metadata["provenance"]["trade_import_version"] == 1
    assert [issue["code"] for issue in metadata["issues"]] == ["out_of_order_trade_prints"]
    assert metadata["replayable"] is True
    first, second, third = store.replay(dataset_id)
    assert first.opened_at == _START
    assert (
        first.open_ticks,
        first.high_ticks,
        first.low_ticks,
        first.close_ticks,
        first.volume,
    ) == (
        29000,
        29002,
        28999,
        28999,
        10,
    )
    assert second.opened_at == first.available_at and second.volume == 5
    assert third.opened_at == second.available_at and third.volume == 6
    all_prints = list(store.trade_prints(dataset_id))
    assert [trade.sequence for trade in all_prints] == list(range(1, 7))
    assert all_prints[0].executed_at.isoformat() == "2025-03-10T13:00:00.000000001+00:00"
    assert all_prints[0].executed_at == all_prints[1].executed_at < all_prints[2].executed_at
    assert all_prints[3].executed_at.isoformat() == "2025-03-10T13:00:59.999999999+00:00"
    stamp = all_prints[0].executed_at.isoformat()
    output = subprocess.run(
        [
            *aurum,
            "trade-prints",
            dataset_id,
            "--after-timestamp",
            stamp,
            "--after-sequence",
            "1",
            "--limit",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    trade = json.loads(output.stdout)
    assert trade["sequence"] == 2 and trade["data_kind"] == "synthetic"
    assert trade["event_id"] == f"{dataset_id}/2"
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    with http.TestClient(api.create_app(store)) as client:
        route = f"/api/datasets/{dataset_id}/trade-prints"
        response = client.get(
            route,
            params={
                "after_timestamp": stamp,
                "after_sequence": 1,
                "limit": 1,
            },
        )
        assert response.status_code == 200
        assert response.json() == [trade]
        assert client.get(route, params={"after_timestamp": stamp}).status_code == 422


def test_trade_import_audits_gaps_without_fabricating_bars(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    source = _write(tmp_path / "synthetic-sparse.csv", (_ROWS[0], _ROWS[-1]))
    dataset_id = store.save(prepare_trade_csv(source, _CONFIG, _CALENDAR))
    metadata = store.describe(dataset_id)
    assert metadata["replayable"] is False
    assert metadata["issues"] == [
        {
            "code": "missing_candles",
            "count": 1,
            "start": (_START + MINUTE).isoformat(),
            "end": (_START + 2 * MINUTE).isoformat(),
        }
    ]
    with pytest.raises(ValueError, match="expected candles are missing"):
        list(store.replay(dataset_id))
    assert len(list(store.trade_prints(dataset_id))) == 2


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (_ROWS[0], "duplicate trade sequence"),
        (_ROWS[0].replace(",2,2900.1,2", ",20,2900.1,0"), "positive integer"),
        (_ROWS[0].replace("MGC-2025-04", "MGC-2025-06"), "expected contract"),
        (_ROWS[0].replace("14:00:00", "14:03:00").replace(",2,", ",20,"), "outside open"),
        (_ROWS[0].replace("000000001+", "0000000001+"), "nine fractional digits"),
        (_ROWS[0].replace("2900.1", "2900.11"), "price must be a multiple"),
    ],
)
def test_trade_import_rejects_invalid_input(tmp_path: Path, row: str, error: str) -> None:
    source = _write(tmp_path / "synthetic-invalid.csv", (_ROWS[0], row))
    with pytest.raises(ValueError, match=error):
        prepare_trade_csv(source, _CONFIG, _CALENDAR)


def test_trade_pages_and_timestamps_preserve_order_below_a_microsecond(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    with store.connection(write=True):
        pass
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE trade_prints")
        db.execute("PRAGMA user_version = 11")
    path = _write(
        tmp_path / "synthetic-many.csv",
        tuple(f"{_FIRST},{_CONTRACT.code},{sequence},2900,1" for sequence in range(1002)),
    )
    dataset_id = store.save(prepare_trade_csv(path, _CONFIG, _CALENDAR))
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    stamps = list(store.trade_prints(dataset_id, limit=1001))
    assert len(stamps) == 1001 and stamps[-1].sequence == 1000
    cutoff = trade_timestamp("2025-03-10T13:00:00.000000001Z")
    assert list(store.trade_prints(dataset_id, end=cutoff)) == []
    assert [trade.sequence for trade in store.trade_prints(dataset_id, after=(cutoff, 1000))] == [
        1001
    ]
    with pytest.raises(ValueError, match="ambiguous or nonexistent"):
        trade_timestamp("2025-10-26T02:15:00.000000007", source_timezone("Europe/Prague"))
