import json
import subprocess
from datetime import date
from hashlib import sha256
from pathlib import Path
from sys import executable

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
