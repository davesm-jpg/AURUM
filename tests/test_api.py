from datetime import date
from pathlib import Path

import pytest

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp


def test_api_reads_validated_history_and_builds_paper_run(tmp_path: Path) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    start = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(start, start + 10 * MINUTE)
    calendar = SessionCalendar(
        "synthetic API fixture",
        "fixture-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    contract = MgcContract("MGC-2025-04")
    path = tmp_path / "synthetic.csv"
    path.write_text(
        "timestamp,contract,open,high,low,close,volume\n"
        + "".join(
            f"{(start + index * MINUTE).isoformat()},{contract.code},2900,2900,2900,2900,1\n"
            for index in range(10)
        ),
        encoding="utf-8",
    )
    store = DatasetStore(tmp_path / "api.sqlite")
    config = ImportConfig(
        "synthetic source", contract, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC
    )
    dataset = store.save(prepare_csv(path, config, calendar))
    client = http.TestClient(api.create_app(store, "synthetic-access"))
    assert client.get("/api/health").status_code == 401
    client.headers["Authorization"] = "Bearer synthetic-access"
    assert client.get("/api/datasets").json()[0]["data_kind"] == "synthetic"
    response = client.get(f"/api/datasets/{dataset}/candles?timeframe=5m&limit=1")
    assert response.status_code == 200
    assert response.json()[0]["available_at"] == span.end.isoformat()
    assert client.get(f"/api/datasets/{dataset}/candles?limit=0").status_code == 422
    assert client.get("/api/datasets/" + "a" * 64).status_code == 404
    created = client.post("/api/backtests", json={"dataset_id": dataset, "variant": "mss"})
    assert created.status_code == 201
    run = created.json()
    assert run["summary"]["performance"]["trades"] == 0
    assert (
        client.get(f"/api/runs/{run['run_id']}/events").json()[0]["event"]
        == "paper_backtest_summary"
    )
    path_to_chart = f"/api/runs/{run['run_id']}/chart-evidence"
    bounds = {"start": start.isoformat(), "through": span.end.isoformat()}
    response = client.get(path_to_chart, params=bounds)
    assert response.status_code == 200
    assert response.json() == {"markers": [], "spans": []}
    inverted = {"start": bounds["through"], "through": bounds["start"]}
    assert client.get(path_to_chart, params=inverted).status_code == 422
