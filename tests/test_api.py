import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Event

import pytest

from aurum.candles import AggregateCandle, CandleAggregator
from aurum.evidence import EvidenceConfig
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.feed_frames import FeedFrame
from aurum.imbalances import ImbalanceConfig
from aurum.instruments import MgcContract
from aurum.liquidity import LiquidityConfig
from aurum.market_data import Candle, DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import (
    MarketMonitor,
    MonitorConfig,
    describe_monitor,
    market_chart_sequence_bounds,
)
from aurum.providers import FeedStatus
from aurum.runtime_profiles import load_delivery_profile, provider_runtime
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
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
    cross_origin = client.post(
        "/api/backtests",
        json={"dataset_id": dataset, "variant": "mss"},
        headers={"Origin": "https://cross-site.invalid"},
    )
    assert cross_origin.status_code == 403
    assert client.get("/api/runs").json() == []
    assert client.get("/api/datasets").json()[0]["data_kind"] == "synthetic"
    response = client.get(f"/api/datasets/{dataset}/candles?timeframe=5m&limit=1")
    assert response.status_code == 200
    assert response.json()[0]["available_at"] == span.end.isoformat()
    assert client.get(f"/api/datasets/{dataset}/candles?limit=0").status_code == 422
    assert client.get("/api/datasets/" + "a" * 64).status_code == 404
    created = client.post(
        "/api/backtests",
        json={"dataset_id": dataset, "variant": "mss"},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
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
    analytics_route = f"/api/backtests/{run['run_id']}/analytics"
    diagnostics = client.get(analytics_route, params={"window": 5}).json()
    assert diagnostics["dataset"]["data_kind"] == "synthetic"
    assert diagnostics["sample_kind"] == "unpartitioned_replay"
    assert diagnostics["rolling_outcomes"] == []
    assert diagnostics["signal_count"] == diagnostics["completed_trades"] == 0
    assert client.get(analytics_route, params={"window": 1}).status_code == 422
    defaults = client.get("/api/configuration/backtest").json()
    assert defaults["execution_mode"] == "paper_research"
    profile = defaults["profile"]
    profile["strategy_config"]["variant"] = "mss"
    request = {"dataset_id": dataset, "timeframe": "5m", "profile": profile}
    assert client.post("/api/backtests", json=request).json()["run_id"] == run["run_id"]
    profile["evidence_config"]["imbalances"]["min_width_ticks"] = 3
    changed = client.post("/api/backtests", json=request)
    assert changed.status_code == 201
    assert changed.json()["run_id"] != run["run_id"]
    assert changed.json()["evidence_config"]["imbalances"]["min_width_ticks"] == 3
    assert client.get(f"/api/backtests/{run['run_id']}").json() == run
    assert client.post("/api/backtests", json={**request, "pivot_left": 4}).status_code == 422
    profile["execution_config"]["latency_minutes"] = 6
    assert client.post("/api/backtests", json=request).status_code == 422
    validation = client.post(
        "/api/reports/validation",
        json={
            "run_ids": [run["run_id"]],
            "validation_start": (start + 3 * MINUTE).isoformat(),
            "holdout_start": (start + 7 * MINUTE).isoformat(),
            "bootstrap_draws": 10,
        },
    )
    assert validation.status_code == 201
    assert validation.json()["kind"] == "chronological_validation"
    assert validation.json()["result"]["holdout"] is None


def test_api_provider_profiles_keep_environment_values_out_of_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    start = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(start, start + MINUTE)
    calendar = SessionCalendar(
        "synthetic profile calendar",
        "synthetic-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    contract = MgcContract("MGC-2025-04")
    path = tmp_path / "synthetic.csv"
    path.write_text(
        "timestamp,contract,open,high,low,close,volume\n"
        f"{start.isoformat()},{contract.code},2900,2900,2900,2900,1\n",
        encoding="utf-8",
    )
    store = DatasetStore(tmp_path / "synthetic-profiles.sqlite")
    config = ImportConfig(
        "synthetic profile source", contract, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC
    )
    dataset = store.save(prepare_csv(path, config, calendar))
    with http.TestClient(api.create_app(store, "synthetic-access")) as client:
        route = "/api/configuration/providers"
        profile = {
            "name": "Synthetic loopback",
            "calendar_dataset_id": dataset,
            "endpoint_env": "AURUM_TEST_ENDPOINT",
            "source_status": "simulated",
        }
        assert client.post(route, json={"profile": profile}).status_code == 401
        client.headers["Authorization"] = "Bearer synthetic-access"
        rejected = client.post(
            route, json={"profile": {**profile, "endpoint_env": "synthetic-value-must-not-echo"}}
        )
        assert rejected.status_code == 422
        assert "synthetic-value-must-not-echo" not in rejected.text
        denied = client.post(route, json={"profile": {**profile, "source_status": "real_time"}})
        assert denied.status_code == 422
        saved = client.post(route, json={"profile": profile})
        assert saved.status_code == 201
        reference = saved.json()["profile_id"]
        assert client.post(route, json={"profile": profile}).json()["profile_id"] == reference
        monkeypatch.setenv("AURUM_TEST_ENDPOINT", "ws://127.0.0.1:18991/synthetic")
        monitor, source, backfill = provider_runtime(store, reference)
        assert monitor.contract == contract and source.mode is FeedStatus.SIMULATED
        assert backfill is None
        listed = client.get("/api/configuration/runtime")
        assert listed.json()[0]["settings"]["endpoint_env"] == "AURUM_TEST_ENDPOINT"
        assert "18991" not in listed.text
        assert "economic_calendar" not in saved.json()["settings"]
        assert "require_clear_event_risk" not in saved.json()["settings"]
        assert "calendar_snapshot" not in saved.json()["settings"]

        invalid = client.post(route, json={"profile": {**profile, "economic_calendar": {}}})
        assert invalid.status_code == 422
        snapshot = {
            "source": "synthetic economic fixture",
            "version": "v1",
            "coverage": TradingWindow(start - 30 * MINUTE, start + 30 * MINUTE).as_dict(),
            "coverage_known_at": (start - 45 * MINUTE).isoformat(),
            "events": [],
        }
        event_profile = {**profile, "economic_calendar": snapshot, "require_clear_event_risk": True}
        gated = client.post(route, json={"profile": event_profile})
        assert gated.status_code == 201
        gated_id = gated.json()["profile_id"]
        assert gated_id != reference
        assert client.post(route, json={"profile": event_profile}).json()["profile_id"] == gated_id
        gated_monitor, _, _ = provider_runtime(store, gated_id)
        assert gated_monitor.monitor_id != monitor.monitor_id
        definition = describe_monitor(store, gated_monitor.monitor_id)["definition"]
        assert isinstance(definition, dict)
        assert definition["economic_calendar"] == snapshot
        assert definition["strategy_config"]["require_clear_event_risk"] is True


def test_provider_profile_can_start_from_explicit_dated_schedule_without_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    store = DatasetStore(tmp_path / "fresh-synthetic.sqlite")
    with store.connection(write=True):
        pass
    start = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(start, start + 10 * MINUTE)
    calendar = SessionCalendar(
        "synthetic standalone schedule",
        "fixture-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    snapshot = {"contract": "MGC-2025-04", "calendar": calendar.as_dict(), "synthetic": True}
    profile = {
        "name": "Standalone synthetic schedule",
        "endpoint_env": "AURUM_SYNTHETIC_TEST_ENDPOINT",
        "source_status": "simulated",
        "calendar_snapshot": snapshot,
    }
    monkeypatch.setenv("AURUM_SYNTHETIC_TEST_ENDPOINT", "ws://127.0.0.1:18992/synthetic")
    with http.TestClient(api.create_app(store)) as client:
        route = "/api/configuration/providers"
        assert client.get("/api/datasets").json() == []
        saved = client.post(route, json={"profile": profile})
        assert saved.status_code == 201
        settings = saved.json()["settings"]
        assert settings["calendar_snapshot"] == snapshot
        assert "calendar_dataset_id" not in settings
        profile_id = saved.json()["profile_id"]
        instance, source, backfill = provider_runtime(store, profile_id)
        assert instance.calendar == calendar
        assert instance.contract == MgcContract("MGC-2025-04")
        assert source.mode is FeedStatus.SIMULATED and backfill is None
        assert client.get("/api/datasets").json() == []
        assert client.post(route, json={"profile": profile}).json()["profile_id"] == profile_id
        for changes in (
            {"calendar_dataset_id": "0" * 64},
            {"calendar_snapshot": None},
            {"source_status": "delayed"},
            {"calendar_snapshot": {"contract": "MGC-2025-04", "calendar": calendar.as_dict()}},
            {"calendar_snapshot": {**snapshot, "contract": "XAUUSD"}},
        ):
            response = client.post(route, json={"profile": {**profile, **changes}})
            assert response.status_code == 422
        legacy = SessionCalendar("synthetic legacy", "v1", span, (span,))
        response = client.post(
            route,
            json={
                "profile": {
                    **profile,
                    "calendar_snapshot": {**snapshot, "calendar": legacy.as_dict()},
                }
            },
        )
        assert response.status_code == 422


def test_api_saves_assumed_and_explicit_synthetic_paper_reports(tmp_path: Path) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    store = DatasetStore(tmp_path / "synthetic-reports.sqlite")
    start = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(start, start + 10 * MINUTE)
    calendar = SessionCalendar(
        "synthetic account calendar",
        "synthetic-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    with http.TestClient(api.create_app(store, "synthetic-access")) as client:
        scenario_route = "/api/reports/monte-carlo"
        scenario = {
            "config": {"paths": 10, "trades": 20, "seed": 19},
            "scenarios": [{"win_rate_bps": 4500}],
        }
        assert client.post(scenario_route, json=scenario).status_code == 401
        client.headers["Authorization"] = "Bearer synthetic-access"
        assert client.get("/api/configuration/reports").json()["account_rules"]["breach_metric"]
        saved = client.post(scenario_route, json=scenario)
        assert saved.status_code == 201
        result = saved.json()
        assert result["result"]["source"] == {"data_kind": "assumed", "observations": 0}
        assert result["result"]["simulations"][0]["config"]["seed"] == 19
        assert client.post(scenario_route, json=scenario).json() == result
        oversized = {**scenario, "config": {"paths": 1_000_000, "trades": 20}}
        assert client.post(scenario_route, json=oversized).status_code == 422
        account = {
            "calendar": calendar.as_dict(),
            "ledger": {
                "data_kind": "synthetic",
                "source": "synthetic account API fixture",
                "started_at": start.isoformat(),
                "actions": [
                    {
                        "kind": "mark",
                        "at": (start + MINUTE).isoformat(),
                        "trade_date": "2025-03-10",
                        "closed_trade_nets_cents": [500],
                    }
                ],
            },
        }
        saved_account = client.post("/api/reports/paper-account", json=account)
        assert saved_account.status_code == 201
        account_report = saved_account.json()
        assert account_report["result"]["data_kind"] == "synthetic"
        assert account_report["result"]["final_state"]["balance_cents"] == 2_500_500
        assert client.get("/api/reports/" + account_report["report_id"]).json() == account_report


def test_api_monitor_chart_excludes_pending_minutes_and_preserves_mixed_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    start = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(start, start + 8 * MINUTE)
    calendar = SessionCalendar(
        "synthetic monitor chart calendar",
        "synthetic-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    contract = MgcContract("MGC-2025-04")
    monitor = MarketMonitor(
        store,
        calendar,
        contract,
        MonitorConfig("synthetic transport testing real-time classification", FeedStatus.REAL_TIME),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
    )
    monitor.set_connection(True, start)

    def send(index: int, received: int, status: FeedStatus = FeedStatus.REAL_TIME) -> None:
        opened = start + index * MINUTE
        candle = Candle(contract, opened, 29000, 29010, 28990, 29000 + index, 1)
        monitor.ingest(FeedFrame.from_replay(candle, status, start + received * MINUTE))

    with http.TestClient(api.create_app(store)) as client:
        prefs = {
            "name": "Synthetic notification profile fixture",
            "monitor_id": monitor.monitor_id,
            "channel": "telegram",
            "destination_env": "AURUM_TEST_CHAT",
            "token_env": "AURUM_TEST_TOKEN",
            "preferences": {
                "quiet_start_minute": 1320,
                "quiet_end_minute": 360,
                "min_score": 70,
                "allowed_sessions": ["new_york"],
            },
        }
        settings = client.post("/api/configuration/deliveries", json={"profile": prefs})
        assert settings.status_code == 201
        loaded = load_delivery_profile(store, settings.json()["profile_id"])
        assert loaded.preferences.min_score == 70
        assert loaded.preferences.allowed_sessions == ("new_york",)
        assert loaded.token_env == "AURUM_TEST_TOKEN"
        route = f"/api/monitors/{monitor.monitor_id}/chart"
        assert client.get(route).json()["candles"] == []
        send(0, 1)
        for index in range(2, 5):
            send(index, index + 1)
        recovering = client.get(route, params={"timeframe": "1m"}).json()
        assert recovering["pending_minutes"] == 3
        assert recovering["alerts_suspended"]
        assert [bar["opened_at"] for bar in recovering["candles"]] == [start.isoformat()]
        assert client.get(route).json()["candles"] == []

        send(1, 5, FeedStatus.HISTORICAL)
        applied = client.get(route).json()
        assert applied["configured_status"] == "real_time"
        assert applied["pending_minutes"] == 0
        assert applied["last_applied_close"] == (start + 5 * MINUTE).isoformat()
        (bar,) = applied["candles"]
        assert bar["available_at"] == applied["last_applied_close"]
        assert bar["source_minutes"] == {"historical": 1, "real_time": 4}
        assert bar["close_ticks"] == 29004
        assert (
            client.get(route, params={"through": (start + 4 * MINUTE).isoformat()}).json()[
                "candles"
            ]
            == []
        )

        aggregating, release = Event(), Event()

        class PausingAggregator(CandleAggregator):
            def push(self, candle: Candle) -> tuple[AggregateCandle, ...]:
                aggregating.set()
                assert release.wait(timeout=8), "synthetic reader was not released"
                return super().push(candle)

        monkeypatch.setattr(api, "CandleAggregator", PausingAggregator)
        with ThreadPoolExecutor(max_workers=1) as pool:
            reading = pool.submit(client.get, route)
            try:
                assert aggregating.wait(timeout=3), "synthetic reader did not start"
                for index in range(5, 8):
                    send(index, index + 1)
            finally:
                release.set()
            assert reading.result(timeout=3).json() == applied
        current = client.get(route).json()
        assert current["last_applied_close"] == span.end.isoformat()
        assert len(current["candles"]) == 2


def test_recovered_synthetic_monitor_evidence_uses_observed_time_on_chart(tmp_path: Path) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    start = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(start, start + 25 * MINUTE)
    calendar = SessionCalendar(
        "synthetic monitor chart calendar",
        "synthetic-gap-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    contract = MgcContract("MGC-2025-04")
    monitor = MarketMonitor(
        store,
        calendar,
        contract,
        MonitorConfig("synthetic recovery", FeedStatus.SIMULATED),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
    )

    def send(index: int, received: int) -> None:
        ticks = 29000 + min(index // 5, 2) * 10
        candle = Candle(contract, start + index * MINUTE, ticks, ticks, ticks, ticks, 1)
        monitor.ingest(
            FeedFrame.from_replay(candle, FeedStatus.SIMULATED, start + received * MINUTE)
        )

    for index in range(17):
        if index != 10:
            send(index, index + 1)
    send(10, 17)
    with http.TestClient(api.create_app(store)) as client:
        root = f"/api/monitors/{monitor.monitor_id}"
        assert client.get(root + "/stream", headers={"Last-Event-ID": "-1"}).status_code == 422
        assert client.get(root + "/stream", params={"mode": "unknown"}).status_code == 422
        source_close = (start + 15 * MINUTE).isoformat()
        observed = (start + 17 * MINUTE).isoformat()
        (gap,) = [
            event
            for event in client.get(root + "/market-events").json()
            if event["event"] == "fvg_confirmed"
        ]
        assert gap["confirmed_at"] == gap["source_received_at"] == source_close
        assert gap["observed_at"] == observed
        prefix = client.get(
            root + "/chart", params={"timeframe": "1m", "through": source_close}
        ).json()
        assert not any(span["category"] == "fvg" for span in prefix["market_evidence"]["spans"])
        assert prefix["context_event"]["observed_at"] == (start + 10 * MINUTE).isoformat()
        later = client.get(root + "/chart", params={"timeframe": "1m"}).json()
        (zone,) = [span for span in later["market_evidence"]["spans"] if span["category"] == "fvg"]
        assert zone["source_sequence"] == gap["sequence"]
        assert minute_timestamp(zone["available_at"]) == minute_timestamp(observed)
        assert later["context_event"]["observed_at"] == observed
        forward = client.post(
            "/api/forward-runs", json={"monitor_id": monitor.monitor_id, "synthetic": True}
        )
        assert forward.status_code == 201
        run = forward.json()
        assert run["data_kind"] == "synthetic_forward_verification"
        assert run["caught_up"]
        route = f"/api/forward-runs/{run['forward_id']}"
        assert client.post(route + "/sync").json()["processed_minutes"] == 0
        assert client.get(route + "/events").json() == []
        report = client.post(
            "/api/reports/forward",
            json={"forward_id": run["forward_id"], "period": "weekly", "trade_date": "2025-03-10"},
        )
        assert report.status_code == 201
        assert report.json()["result"]["period"]["outcomes"]["trades"] == 0
        assert report.json()["result"]["period"]["coverage"]["applied_forward_minutes"] == 0


def test_indexed_synthetic_monitor_projection_matches_full_journal_after_recovery(
    tmp_path: Path,
) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    start = minute_timestamp("2025-03-10T13:00Z")
    span = TradingWindow(start, start + 160 * MINUTE)
    calendar = SessionCalendar(
        "synthetic projection fixture",
        "synthetic-index-v1",
        span,
        sessions=(TradingSession(date(2025, 3, 10), (span,)),),
    )
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    contract = MgcContract("MGC-2025-04")
    monitor = MarketMonitor(
        store,
        calendar,
        contract,
        MonitorConfig("synthetic bounded evidence", FeedStatus.SIMULATED),
        DEFAULT_STRUCTURE_CONFIG,
        DEFAULT_STRATEGY_CONFIG,
        EvidenceConfig(
            liquidity=LiquidityConfig(max_level_age_bars=3),
            imbalances=ImbalanceConfig(max_age_bars=3),
        ),
    )

    def send(index: int, observed: int) -> None:
        ticks = 29000 + (index // 5 % 8) * 10
        candle = Candle(contract, start + index * MINUTE, ticks, ticks, ticks, ticks, 1)
        monitor.ingest(
            FeedFrame.from_replay(candle, FeedStatus.SIMULATED, start + observed * MINUTE)
        )

    for index in range(160):
        if index != 145:
            send(index, index + 1)
        if index == 150:
            send(145, 151)
    through = start + 154 * MINUTE
    left = through - 12 * MINUTE
    checkpoint = (span.end - MINUTE).isoformat()
    after, ceiling = market_chart_sequence_bounds(
        store, monitor.monitor_id, checkpoint, left, through, 5
    )
    assert ceiling is not None and 0 < after < ceiling
    params = {"limit": 12, "timeframe": "1m", "through": through.isoformat()}
    with http.TestClient(api.create_app(store)) as client:
        route = f"/api/monitors/{monitor.monitor_id}/chart"
        indexed = client.get(route, params=params).json()
        assert indexed["market_evidence"]["spans"]
        with sqlite3.connect(store.path) as db:
            db.execute("PRAGMA user_version = 9")
        assert client.get(route, params=params).json() == indexed
