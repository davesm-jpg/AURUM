import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from sys import executable
from types import SimpleNamespace

import pytest

from aurum.databento_csv import DATABENTO_OHLCV_COLUMNS
from aurum.dataset_audit import verify_dataset
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_history
from aurum.providers import FileHistorySource, SourceFormat
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-03-10T13:00Z")
CONTRACT = MgcContract("MGC-2025-04")
SPAN = TradingWindow(START, START + 3 * MINUTE)
CALENDAR = SessionCalendar(
    "synthetic Databento fixture",
    "fixture-v1",
    SPAN,
    sessions=(TradingSession(date(2025, 3, 10), (SPAN,)),),
)
CONFIG = ImportConfig(
    "synthetic Databento export", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC
)


def test_constructed_synthetic_dbn_exports_through_pinned_sdk_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = pytest.importorskip("databento")
    dbn = pytest.importorskip("databento_dbn")

    def no_client(*_: object, **__: object) -> None:
        raise AssertionError("synthetic export must not create a network client")

    monkeypatch.setattr(sdk, "Historical", no_client)
    monkeypatch.setattr(sdk, "Live", no_client)

    def nanos(stamp: datetime) -> int:
        return (stamp - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000

    mapping = SimpleNamespace(
        raw_symbol="MGCJ5",
        intervals=[
            SimpleNamespace(start_date=date(2025, 3, 10), end_date=date(2025, 3, 11), symbol="1234")
        ],
    )
    metadata = dbn.Metadata(
        dataset="GLBX.MDP3",
        start=nanos(SPAN.start),
        end=nanos(SPAN.end),
        stype_in=dbn.SType.RAW_SYMBOL,
        stype_out=dbn.SType.INSTRUMENT_ID,
        schema=dbn.Schema.OHLCV_1M,
        symbols=["MGCJ5"],
        mappings=[mapping],
    )
    records = [
        dbn.OHLCVMsg(
            rtype=dbn.RType.OHLCV_1M,
            publisher_id=1,
            instrument_id=1234,
            ts_event=nanos(START + minute * MINUTE),
            open=2_900_000_000_000,
            high=2_900_200_000_000,
            low=2_899_900_000_000,
            close=2_900_100_000_000,
            volume=5,
        )
        for minute in range(3)
    ]
    export = tmp_path / "constructed-synthetic-sdk-export.csv"
    sdk.DBNStore.from_bytes(bytes(metadata) + b"".join(bytes(record) for record in records)).to_csv(
        export, pretty_ts=True, pretty_px=True, map_symbols=True
    )
    assert export.read_text().splitlines()[0] == ",".join(DATABENTO_OHLCV_COLUMNS)
    prepared = prepare_history(
        FileHistorySource(export, SourceFormat.DATABENTO_OHLCV_CSV), CONFIG, CALENDAR
    )
    store = DatasetStore(tmp_path / "synthetic-sdk-roundtrip.sqlite")
    dataset = store.save(prepared)
    candles = list(store.replay(dataset))
    assert len(candles) == 3 and sum(candle.volume for candle in candles) == 15
    assert candles[0].open_ticks == 29000 and candles[-1].close_ticks == 29001
    assert [candle.opened_at for candle in candles] == [
        START + minute * MINUTE for minute in range(3)
    ]
    assert store.source_bytes(dataset) == export.read_bytes()
    assert verify_dataset(store, dataset)["verified"] is True


def write_export(path: Path, rows: list[list[str]]) -> Path:
    path.write_text(
        ",".join(DATABENTO_OHLCV_COLUMNS) + "\n" + "\n".join(",".join(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def synthetic_row(minute: int) -> list[str]:
    return [
        (START + minute * MINUTE).isoformat(),
        "33",
        "1",
        "1234",
        "2900.000000000",
        "2900.200000000",
        "2899.900000000",
        "2900.100000000",
        "5",
        "MGCJ5",
    ]


def test_native_export_import_preserves_raw_provenance_and_reproduces(tmp_path: Path) -> None:
    source = write_export(
        tmp_path / "synthetic-databento.csv", [synthetic_row(i) for i in (2, 0, 1)]
    )
    prepared = prepare_history(
        FileHistorySource(source, SourceFormat.DATABENTO_OHLCV_CSV), CONFIG, CALENDAR
    )
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    dataset = store.save(prepared)
    assert store.save(prepared) == dataset
    assert store.source_bytes(dataset) == source.read_bytes()
    metadata = store.describe(dataset)
    assert metadata["provenance"]["source_sha256"] == sha256(source.read_bytes()).hexdigest()
    assert metadata["provenance"]["databento_normalization_version"] == 1
    assert metadata["provenance"]["source_timestamp_basis"] == "vendor_receive_interval"
    assert metadata["provenance"]["source_empty_interval_policy"] == "omitted_without_price_filling"
    assert metadata["issues"] == [{"code": "out_of_order", "count": 2, "start": None, "end": None}]
    candles = list(store.replay(dataset))
    assert len(candles) == 3 and candles[0].opened_at == START
    assert candles[0].available_at == START + MINUTE
    assert candles[0].open_ticks == 29000 and candles[0].volume == 5
    assert verify_dataset(store, dataset)["verified"] is True
    calendar_file = tmp_path / "synthetic-calendar.json"
    calendar_file.write_text(json.dumps(CALENDAR.as_dict()), encoding="utf-8")
    result = subprocess.run(
        [
            str(Path(executable).with_name("aurum")),
            "--db",
            str(store.path),
            "import-databento-csv",
            str(source),
            "--calendar",
            str(calendar_file),
            "--provider",
            CONFIG.provider,
            "--contract",
            CONTRACT.code,
            "--source-timezone",
            "UTC",
            "--data-kind",
            "synthetic",
            "--price-basis",
            "unadjusted",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["dataset_id"] == dataset


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (1, "32", "rtype 33"),
        (9, "GCJ5", "dated MGC"),
        (9, "MGCJ5-MGCM5", "dated MGC"),
        (9, "MGCM5", "dated MGC"),
        (9, "MGCJ6", "dated MGC"),
        (4, "2900000000000", "pretty_px"),
        (0, "1741611600000000000", "pretty_ts"),
        (0, "2025-03-10T13:00:00.000000001Z", "whole UTC minute"),
        (3, "0", "instrument ID"),
    ],
)
def test_native_export_rejects_ambiguous_encoding_and_contracts(
    tmp_path: Path, column: int, value: str, message: str
) -> None:
    row = synthetic_row(0)
    row[column] = value
    source = write_export(tmp_path / "synthetic-invalid.csv", [row])
    with pytest.raises(ValueError, match=message):
        prepare_history(
            FileHistorySource(source, SourceFormat.DATABENTO_OHLCV_CSV), CONFIG, CALENDAR
        )


def test_native_export_preserves_missing_minutes_and_blocks_replay(tmp_path: Path) -> None:
    source = write_export(tmp_path / "synthetic-sparse.csv", [synthetic_row(0), synthetic_row(2)])
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    dataset = store.save(
        prepare_history(
            FileHistorySource(source, SourceFormat.DATABENTO_OHLCV_CSV), CONFIG, CALENDAR
        )
    )
    assert store.describe(dataset)["candle_count"] == 2
    assert store.describe(dataset)["replayable"] is False
    assert verify_dataset(store, dataset)["verified"] is True
    with pytest.raises(ValueError, match="missing"):
        list(store.replay(dataset))


def test_native_export_rejects_mixed_instrument_identity_and_duplicate_minutes(
    tmp_path: Path,
) -> None:
    first, second = synthetic_row(0), synthetic_row(1)
    second[3] = "1235"
    source = write_export(tmp_path / "synthetic-mixed.csv", [first, second])
    with pytest.raises(ValueError, match="one publisher and instrument"):
        prepare_history(
            FileHistorySource(source, SourceFormat.DATABENTO_OHLCV_CSV), CONFIG, CALENDAR
        )
    source = write_export(tmp_path / "synthetic-duplicate.csv", [first, first])
    with pytest.raises(ValueError, match="duplicate candle"):
        prepare_history(
            FileHistorySource(source, SourceFormat.DATABENTO_OHLCV_CSV), CONFIG, CALENDAR
        )
