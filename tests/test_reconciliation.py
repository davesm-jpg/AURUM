import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from sys import executable
from zoneinfo import ZoneInfo

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.reconciliation import save_reconciliation
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp

_START = minute_timestamp("2025-03-10T13:00Z")
_TRADE_DATE = date(2025, 3, 10)
_CONTRACT = MgcContract("MGC-2025-04")
_COVERAGE = TradingWindow(_START, _START + 6 * MINUTE)


def _calendar(*, alternative: bool = False) -> SessionCalendar:
    first = TradingWindow(_START, _START + 3 * MINUTE)
    second = TradingWindow(_START + 4 * MINUTE, _COVERAGE.end)
    sessions = (
        (TradingSession(_TRADE_DATE, (TradingWindow(_START, _COVERAGE.end),)),)
        if alternative
        else (
            TradingSession(_TRADE_DATE, (first,)),
            TradingSession(_TRADE_DATE + timedelta(days=1), (second,)),
        )
    )
    return SessionCalendar(
        "synthetic reconciliation schedule", "test-v1", _COVERAGE, sessions=sessions
    )


def _save(
    store: DatasetStore,
    label: str,
    calendar: SessionCalendar,
    minutes: tuple[int, ...],
    *,
    close_labels: bool = False,
    differing_minute: int | None = None,
    contract: MgcContract = _CONTRACT,
) -> str:
    source = store.path.parent / f"{label}.csv"
    rows = ["timestamp,contract,open,high,low,close,volume"]
    for minute in minutes:
        stamp = _START + minute * MINUTE
        if close_labels:
            stamp = (stamp + MINUTE).astimezone(ZoneInfo("Europe/Prague"))
        close, volume = ("2900.1", 3) if minute == differing_minute else ("2900.0", 2)
        rows.append(f"{stamp.isoformat()},{contract.code},2900,2900.2,2899.9,{close},{volume}")
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    config = ImportConfig(
        f"synthetic {label}",
        contract,
        "Europe/Prague" if close_labels else "UTC",
        TimestampLabel.CLOSE if close_labels else TimestampLabel.OPEN,
        DataKind.SYNTHETIC,
    )
    return store.save(prepare_csv(source, config, calendar))


def test_reconciliation_reports_gaps_calendar_disagreement_and_exact_value_diffs(
    tmp_path: Path,
) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    primary = _save(store, "primary", _calendar(), (0, 2, 4, 5))
    comparison = _save(
        store,
        "comparison",
        _calendar(alternative=True),
        (0, 1, 3, 4, 5),
        close_labels=True,
        differing_minute=4,
    )
    with pytest.raises(ValueError, match="expected candles are missing"):
        list(store.replay_batches(primary, _COVERAGE))

    report_id = save_reconciliation(store, primary, comparison, example_limit=1)
    report = store.describe_analysis(report_id)
    result = report["result"]
    assert isinstance(result, dict)
    assert result["data_kind"] == "synthetic"
    assert result["exact_on_window"] is False
    assert result["counts"] == {
        "calendar_open_disagreement": 1,
        "compared_ohlcv": 3,
        "comparison_missing": 1,
        "comparison_observed": 5,
        "comparison_scheduled": 6,
        "exact_ohlcv_matches": 2,
        "ohlcv_mismatch": 1,
        "primary_missing": 1,
        "primary_observed": 4,
        "primary_scheduled": 5,
        "scheduled_in_both": 5,
        "scheduled_in_either": 6,
        "trade_date_disagreement": 2,
    }
    assert result["ohlcv_field_mismatches"] == {
        "close_ticks": 1,
        "high_ticks": 0,
        "low_ticks": 0,
        "open_ticks": 0,
        "volume": 1,
    }
    assert len(result["examples"]["trade_date_disagreement"]) == 1
    (mismatch,) = result["examples"]["ohlcv_mismatch"]
    assert mismatch["opened_at"] == (_START + 4 * MINUTE).isoformat()
    assert mismatch["primary"]["ohlcv"]["close_ticks"] == 29000
    assert mismatch["comparison"]["ohlcv"]["close_ticks"] == 29001
    assert result["comparison"]["timestamp_label"] == "close"
    assert result["comparison"]["source_timezone"] == "Europe/Prague"
    assert result["primary"]["replayable"] is False
    assert save_reconciliation(store, primary, comparison, example_limit=1) == report_id
    with pytest.raises(ValueError, match="expected candles are missing"):
        list(store.replay(primary))


def test_reconciliation_exact_subset_cli_api_and_scope_validation(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    primary = _save(store, "primary", _calendar(), (0, 2, 4, 5))
    comparison = _save(store, "comparison", _calendar(), (0, 1, 2, 4, 5), close_labels=True)
    window = TradingWindow(_START + 2 * MINUTE, _COVERAGE.end)
    report_id = save_reconciliation(store, primary, comparison, span=window)
    report = store.describe_analysis(report_id)
    result = report["result"]
    assert isinstance(result, dict)
    assert result["exact_on_window"] is True
    assert result["primary"]["replayable"] is False
    assert result["counts"]["exact_ohlcv_matches"] == 3
    assert result["identical_raw_checksum"] is False

    args = [
        str(Path(executable).with_name("aurum")),
        "--db",
        str(store.path),
        "reconcile-datasets",
        primary,
        comparison,
        "--start",
        window.start.isoformat(),
        "--end",
        window.end.isoformat(),
    ]
    output = subprocess.run(args, check=True, capture_output=True, text=True)
    assert json.loads(output.stdout) == report
    with pytest.raises(ValueError, match="within both dataset calendar"):
        save_reconciliation(
            store,
            primary,
            comparison,
            span=TradingWindow(_START - MINUTE, _COVERAGE.end),
        )
    with pytest.raises(ValueError, match="two distinct imports"):
        save_reconciliation(store, primary, primary)
    different_contract = _save(
        store,
        "other-contract",
        _calendar(),
        (0, 2, 4, 5),
        contract=MgcContract("MGC-2025-06"),
    )
    with pytest.raises(ValueError, match="share contract"):
        save_reconciliation(store, primary, different_contract)

    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    with http.TestClient(api.create_app(store)) as client:
        payload = {
            "primary_dataset_id": primary,
            "comparison_dataset_id": comparison,
            **window.as_dict(),
        }
        saved = client.post("/api/reports/reconciliation", json=payload)
        assert saved.status_code == 201
        assert saved.json() == report
        del payload["end"]
        assert client.post("/api/reports/reconciliation", json=payload).status_code == 422


def test_reconciliation_discloses_identical_raw_and_unknown_trade_dates(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    dated = _calendar()
    legacy = SessionCalendar("synthetic legacy", "v1", _COVERAGE, windows=dated.windows)
    primary = _save(store, "dated", dated, (0, 1, 2, 4, 5))
    comparison = _save(store, "legacy", legacy, (0, 1, 2, 4, 5))
    result = store.describe_analysis(save_reconciliation(store, primary, comparison))["result"]
    assert isinstance(result, dict)
    assert result["identical_raw_checksum"] is True
    assert result["trade_dates_comparable"] is False
    assert result["counts"]["exact_ohlcv_matches"] == 5
    assert result["exact_on_window"] is False

    closure = TradingWindow(_START + 3 * MINUTE, _START + 4 * MINUTE)
    matching = _save(store, "same dated window", dated, (0, 1, 2, 4, 5))
    empty_id = save_reconciliation(store, primary, matching, span=closure)
    empty = store.describe_analysis(empty_id)["result"]
    assert isinstance(empty, dict)
    assert empty["counts"]["scheduled_in_either"] == 0
    assert empty["exact_on_window"] is False
