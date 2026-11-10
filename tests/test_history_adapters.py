import json
import subprocess
from datetime import date
from io import BytesIO
from pathlib import Path
from sys import executable
from urllib.request import Request

import pytest

from aurum.dataset_audit import verify_dataset
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_csv, prepare_history
from aurum.providers import FileHistorySource, HttpsHistorySource, SourceFormat
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-03-10T13:00Z")
CONTRACT = MgcContract("MGC-2025-04")
CONFIG = ImportConfig("synthetic source", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC)
HEADER = "timestamp,contract,open,high,low,close,volume\n"
RAW = (
    HEADER
    + "2025-03-10T13:00Z,MGC-2025-04,2900,2900.2,2899.9,2900.1,2\n"
    + "2025-03-10T13:01Z,MGC-2025-04,2900.1,2900.3,2900,2900.2,3\n"
).encode()


def calendar() -> SessionCalendar:
    span = TradingWindow(START, START + 2 * MINUTE)
    return SessionCalendar(
        "synthetic schedule",
        "fixture-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )


def test_native_parquet_and_csv_share_exact_normalization(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")
    source = tmp_path / "fixture.parquet"
    arrow_data = pa.table(
        {
            "volume": [2, 3],
            "timestamp": pa.array([START, START + MINUTE], type=pa.timestamp("ns", tz="UTC")),
            "contract": [CONTRACT.code, CONTRACT.code],
            "open": [2900.0, 2900.1],
            "high": [2900.2, 2900.3],
            "low": [2899.9, 2900.0],
            "close": [2900.1, 2900.2],
        }
    )
    parquet.write_table(arrow_data, source)
    csv = tmp_path / "fixture.csv"
    csv.write_bytes(RAW)
    equivalent = prepare_csv(csv, CONFIG, calendar())
    prepared = prepare_history(FileHistorySource(source, SourceFormat.PARQUET), CONFIG, calendar())
    assert prepared.candles == equivalent.candles
    assert prepared.dataset_id != equivalent.dataset_id
    assert prepared.issues == ()
    assert prepared.raw == source.read_bytes()
    db = tmp_path / "history.sqlite"
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps(calendar().as_dict()), encoding="utf-8")
    args = [
        str(Path(executable).with_name("aurum")),
        "--db",
        str(db),
        "import-parquet",
        str(source),
        "--calendar",
        str(schedule),
        "--contract",
        CONTRACT.code,
        "--provider",
        CONFIG.provider,
        "--source-timezone",
        "UTC",
        "--timestamp-label",
        "open",
        "--data-kind",
        "synthetic",
        "--price-basis",
        "unadjusted",
    ]
    output = subprocess.run(args, check=True, capture_output=True, text=True)
    manifest = json.loads(output.stdout)
    assert manifest["dataset_id"] == prepared.dataset_id
    assert manifest["provenance"]["format"] == "parquet"
    assert DatasetStore(db).source_bytes(prepared.dataset_id) == prepared.raw
    assert verify_dataset(DatasetStore(db), prepared.dataset_id)["source_format"] == "parquet"


@pytest.mark.parametrize("case", ["nanoseconds", "contract", "gap"])
def test_parquet_preserves_precision_and_uses_quality_gate(tmp_path: Path, case: str) -> None:
    pa = pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")
    source = tmp_path / "fixture.parquet"
    epoch_ns = int(START.timestamp()) * 1_000_000_000
    nanos = epoch_ns + (1 if case == "nanoseconds" else 0)
    contract = "MGC-2025-06" if case == "contract" else CONTRACT.code
    parquet.write_table(
        pa.table(
            {
                "timestamp": pa.array([nanos], type=pa.int64()).cast(pa.timestamp("ns", tz="UTC")),
                "contract": [contract],
                "open": [2900.0],
                "high": [2900.2],
                "low": [2899.9],
                "close": [2900.1],
                "volume": [2],
            }
        ),
        source,
    )
    adapter = FileHistorySource(source, SourceFormat.PARQUET)
    if case == "gap":
        dataset = prepare_history(adapter, CONFIG, calendar())
        assert dataset.issues[0].code == "missing_candles"
        store = DatasetStore(tmp_path / "test.sqlite")
        dataset_id = store.save(dataset)
        with pytest.raises(ValueError, match="replay blocked"):
            next(store.replay(dataset_id))
    else:
        with pytest.raises(
            ValueError, match="whole minute" if case == "nanoseconds" else "contract"
        ):
            prepare_history(adapter, CONFIG, calendar())


def test_https_adapter_keeps_auth_and_query_out_of_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class SyntheticOpener:
        def open(self, request: Request, *, timeout: int) -> BytesIO:
            assert request.get_header("Authorization") == "Bearer synthetic-access"
            assert timeout == 30
            return BytesIO(RAW)

    monkeypatch.setenv("AURUM_SYNTHETIC_TEST_TOKEN", "synthetic-access")
    monkeypatch.setattr("aurum.providers.build_opener", lambda _: SyntheticOpener())
    source = HttpsHistorySource(
        "https://data.invalid/export?query=synthetic-private",
        SourceFormat.CSV,
        "AURUM_SYNTHETIC_TEST_TOKEN",
    )
    dataset = prepare_history(source, CONFIG, calendar())
    provenance = json.loads(dataset.provenance_json)
    assert provenance["source_origin"] == "https://data.invalid"
    assert "synthetic-access" not in dataset.provenance_json
    assert "synthetic-private" not in dataset.provenance_json
    assert dataset.raw == RAW
    store = DatasetStore(tmp_path / "synthetic-http.sqlite")
    dataset_id = store.save(dataset)

    def unavailable(_: object) -> None:
        pytest.fail("verification attempted to reconnect to the historical provider")

    monkeypatch.delenv("AURUM_SYNTHETIC_TEST_TOKEN")
    monkeypatch.setattr("aurum.providers.build_opener", unavailable)
    assert verify_dataset(store, dataset_id)["verified"] is True
    with pytest.raises(ValueError, match="must be HTTPS"):
        HttpsHistorySource("http://data.invalid/export", SourceFormat.CSV)
    with pytest.raises(ValueError, match="embedded credentials"):
        HttpsHistorySource("https://user:data@data.invalid/export", SourceFormat.CSV)
