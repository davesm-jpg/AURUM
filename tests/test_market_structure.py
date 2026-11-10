import json
import sqlite3
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from sys import executable
from threading import Barrier, Event

import pytest

from aurum.candles import AggregateCandle, Timeframe
from aurum.control import ControlConfig, save_matched_control
from aurum.economic_events import Announcement, EconomicCalendar
from aurum.evidence import EvidenceConfig
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.execution import DEFAULT_EXECUTION_CONFIG
from aurum.feed_frames import FeedFrame
from aurum.forward import ForwardPaper, forward_events, register_forward
from aurum.forward_reports import ForwardPeriod, save_forward_report
from aurum.imbalances import ImbalanceConfig
from aurum.instruments import MgcContract
from aurum.liquidity import LiquidityConfig
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.market_structure import (
    BreakMode,
    BreakRule,
    Direction,
    PivotRule,
    Relation,
    Scope,
    Side,
    StructureBreak,
    StructureConfig,
    StructureEngine,
    StructureEvent,
    Swing,
    Trend,
    TrendChange,
)
from aurum.monitoring import MarketMonitor, MonitorConfig, market_events_page, monitor_events
from aurum.providers import FeedStatus
from aurum.research_reports import backtest_analytics, validate_runs
from aurum.statistics import ValidationConfig
from aurum.storage import SCHEMA_VERSION, DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, SetupState, StrategyReplay, Variant
from aurum.timestamps import MINUTE, minute_timestamp

CONTRACT = MgcContract("MGC-2025-04")
TIMEFRAME = Timeframe.FIVE_MINUTES
TRADE_DATE = date(2025, 3, 10)
START = minute_timestamp("2025-03-10T13:00Z")
# Invented prices for testing an objectively bearish sequence.
BEARISH = [
    (100, 108),
    (104, 120),
    (98, 112),
    (91, 107),
    (96, 111),
    (95, 115),
    (90, 109),
    (87, 104),
    (92, 110),
]


def synthetic_bar(
    index: int,
    low: int,
    high: int,
    close: int | None = None,
    open_: int | None = None,
    *,
    mirror: bool = False,
) -> AggregateCandle:
    opened_at = START + MINUTE * (5 * index)
    close = (low + high) // 2 if close is None else close
    open_ = (low + high) // 2 if open_ is None else open_
    if mirror:
        low, high, close, open_ = 300 - high, 300 - low, 300 - close, 300 - open_
    return AggregateCandle(
        CONTRACT,
        TRADE_DATE,
        TIMEFRAME,
        opened_at,
        opened_at + MINUTE * 5,
        open_,
        high,
        low,
        close,
        10,
        5,
        False,
    )


def feed(
    bars: Iterable[AggregateCandle], *, config: StructureConfig | None = None
) -> list[StructureEvent]:
    engine = StructureEngine(CONTRACT, TIMEFRAME, config or StructureConfig(PivotRule(1, 1)))
    return [event for bar in bars for event in engine.push(bar)]


def internal_breaks(events: Iterable[StructureEvent]) -> list[StructureBreak]:
    return [
        event
        for event in events
        if isinstance(event, StructureBreak) and event.pivot.scope is Scope.INTERNAL
    ]


def test_pivot_appears_only_on_final_right_bar_and_scopes_are_independent() -> None:
    bars = [synthetic_bar(index, 90, high) for index, high in enumerate([101, 104, 111, 106, 105])]
    config = StructureConfig(PivotRule(1, 2), PivotRule(2, 2))
    engine = StructureEngine(CONTRACT, TIMEFRAME, config)
    for bar in bars[:-1]:
        assert engine.push(bar) == ()
    events = engine.push(bars[-1])
    assert len(events) == 2
    for event, scope in zip(events, Scope, strict=True):
        assert isinstance(event, Swing)
        assert event.scope is scope
        assert event.side is Side.HIGH
        assert event.price_ticks == 111
        assert event.pivot_opened_at == bars[2].opened_at
        assert event.confirmation_opened_at == bars[4].opened_at
        assert event.confirmed_at == bars[4].available_at


def test_tied_neighbors_are_rejected_and_equal_distinct_swings_are_classified() -> None:
    bounds = [
        (93, 100),
        (93, 110),
        (91, 110),
        (90, 100),
        (93, 112),
        (90, 103),
        (93, 112),
        (92, 104),
    ]
    events = feed(synthetic_bar(index, *bound) for index, bound in enumerate(bounds))
    highs = [event for event in events if isinstance(event, Swing) and event.side is Side.HIGH]
    assert [event.relation for event in highs] == [Relation.INITIAL, Relation.EQUAL_HIGH]
    assert [event.price_ticks for event in highs] == [112, 112]
    assert any(
        isinstance(event, TrendChange) and event.current is Trend.CONSOLIDATION for event in events
    )


@pytest.mark.parametrize("mirror", [False, True])
def test_mss_requires_opposite_prior_structure_and_tracks_invalidation(mirror: bool) -> None:
    bars = [synthetic_bar(index, *bound, mirror=mirror) for index, bound in enumerate(BEARISH)]
    breakout = synthetic_bar(9, 91, 117, 116, 98, mirror=mirror)
    follow = synthetic_bar(10, 91, 120, 118, 102, mirror=mirror)
    events = feed([*bars, breakout, follow])
    breaks = internal_breaks(events)
    shifts = [event for event in breaks if event.invalidation_pivot is not None]
    (mss,) = shifts
    expected_direction = Direction.BEARISH if mirror else Direction.BULLISH
    assert mss.direction is expected_direction
    assert mss.prior is (Trend.BULLISH if mirror else Trend.BEARISH)
    assert mss.pivot.confirmed_at < breakout.available_at
    assert mss.confirmed_at == breakout.available_at
    assert mss.break_opened_at == breakout.opened_at
    assert mss.extension_ticks == 1
    assert mss.invalidation_pivot is not None
    assert mss.invalidation_pivot.price_ticks == (213 if mirror else 87)
    assert mss.displacement.body_range_bps == 6923
    assert mss.as_dict()["event"] == "mss_confirmed"
    assert sum(event.direction is expected_direction for event in breaks) == 1
    assert any(
        isinstance(event, TrendChange) and event.current is Trend.TRANSITION for event in events
    )


def test_break_uses_older_level_before_same_close_confirms_replacement() -> None:
    bars = [
        synthetic_bar(0, 90, 99),
        synthetic_bar(1, 90, 100),
        synthetic_bar(2, 90, 99),
        synthetic_bar(3, 90, 110, 99),
        synthetic_bar(4, 90, 105, 104),
    ]
    events = feed(bars)
    (event,) = internal_breaks(events)
    assert event.pivot.price_ticks == 100
    assert event.prior is Trend.UNKNOWN
    assert event.invalidation_pivot is None
    assert event.confirmed_at == bars[4].available_at
    (replacement,) = [
        item
        for item in events
        if isinstance(item, Swing) and item.confirmed_at == bars[4].available_at
    ]
    assert replacement.price_ticks == 110


def test_two_way_wick_breaks_are_bos_without_order_inference() -> None:
    bars = [synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)]
    straddled = synthetic_bar(9, 85, 118, 99)
    config = StructureConfig(PivotRule(1, 1), breaks=BreakRule(BreakMode.WICK))
    breaks = [
        event
        for event in internal_breaks(feed([*bars, straddled], config=config))
        if event.confirmed_at == straddled.available_at
    ]
    assert [event.direction for event in breaks] == [Direction.BULLISH, Direction.BEARISH]
    assert all(event.invalidation_pivot is None for event in breaks)


@pytest.mark.parametrize(
    ("mode", "expected_index"),
    [
        (BreakMode.WICK, 9),
        (BreakMode.CLOSE, 10),
        (BreakMode.BUFFERED_CLOSE, 11),
        (BreakMode.DISPLACEMENT_CLOSE, 11),
    ],
)
def test_rule_variants_first_qualifying_break(mode: BreakMode, expected_index: int) -> None:
    bars = [
        *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 96, 118, 112, 111),
        synthetic_bar(10, 96, 118, 116, 115),
        synthetic_bar(11, 98, 150, 147, 101),
    ]
    config = StructureConfig(
        PivotRule(1, 1), breaks=BreakRule(mode, atr_period=2, atr_buffer_bps=10_000)
    )
    bullish = [
        event
        for event in internal_breaks(feed(bars, config=config))
        if event.direction is Direction.BULLISH
    ]
    (event,) = bullish
    assert event.confirmed_at == bars[expected_index].available_at
    assert event.mode is mode
    if expected_index == 11:
        assert event.displacement.prior_true_range_sum == 44
        assert event.displacement.atr_period == 2


def test_volatility_rules_wait_for_full_prior_window() -> None:
    bars = [
        *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 95, 135, 130, 97),
        synthetic_bar(10, 95, 135, 130, 97),
    ]
    config = StructureConfig(
        PivotRule(1, 1), breaks=BreakRule(BreakMode.BUFFERED_CLOSE, atr_period=10)
    )
    (event,) = internal_breaks(feed(bars, config=config))
    assert event.confirmed_at == bars[10].available_at
    assert event.displacement.prior_true_range_sum is not None


def test_future_candles_do_not_change_existing_events() -> None:
    prefix = [synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)]
    cutoff = prefix[-1].available_at
    expected = feed(prefix)
    for future in (
        [synthetic_bar(9, 91, 117, 116), synthetic_bar(10, 91, 120, 118)],
        [synthetic_bar(9, 80, 90, 81), synthetic_bar(10, 70, 82, 80)],
    ):
        assert [
            event for event in feed([*prefix, *future]) if event.confirmed_at <= cutoff
        ] == expected


def test_out_of_order_input_is_rejected_before_state_changes() -> None:
    engine = StructureEngine(CONTRACT, TIMEFRAME, StructureConfig(PivotRule(1, 1)))
    first = synthetic_bar(0, 92, 101)
    assert engine.push(first) == ()
    with pytest.raises(ValueError, match="chronological"):
        engine.push(first)
    assert engine.push(synthetic_bar(1, 92, 110)) == ()
    (swing,) = engine.push(synthetic_bar(2, 92, 103))
    assert isinstance(swing, Swing)
    assert swing.price_ticks == 110


def prepare_synthetic_replay(
    tmp_path: Path, *, with_gap: bool = False, bars: list[AggregateCandle] | None = None
) -> tuple[DatasetStore, str]:
    if bars is None:
        bars = [
            *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
            synthetic_bar(9, 91, 117, 116),
        ]
    span = TradingWindow(bars[0].opened_at, bars[-1].available_at)
    calendar = SessionCalendar(
        "synthetic market structure fixture",
        "fixture-v1",
        span,
        sessions=(TradingSession(TRADE_DATE, (span,)),),
    )
    path = tmp_path / "synthetic.csv"
    with path.open("w", encoding="utf-8") as file:
        file.write("timestamp,contract,open,high,low,close,volume\n")
        for bar in bars:
            for index in range(5):
                if with_gap and bar is bars[-1] and index == 4:
                    continue
                opened = (bar.opened_at + index * MINUTE).isoformat()
                file.write(
                    f"{opened},{CONTRACT.code},{bar.open_ticks / 10:.1f},"
                    f"{bar.high_ticks / 10:.1f},{bar.low_ticks / 10:.1f},"
                    f"{bar.close_ticks / 10:.1f},2\n"
                )
    config = ImportConfig(
        "synthetic test", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC
    )
    store = DatasetStore(tmp_path / "synthetic.sqlite")
    return store, store.save(prepare_csv(path, config, calendar))


def test_cli_structure_runs_persist_and_version_their_evidence(tmp_path: Path) -> None:
    store, dataset_id = prepare_synthetic_replay(tmp_path)
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    run = subprocess.run(
        [*aurum, "structure", dataset_id, "--internal-pivot", "1", "1"],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = json.loads(run.stdout)
    run_id = manifest["run_id"]
    assert manifest["dataset_id"] == dataset_id
    assert manifest["contract"] == CONTRACT.code
    assert manifest["structure_version"] == 1
    assert manifest["data_kind"] == "synthetic"

    emitted = subprocess.run(
        [*aurum, "structure-events", run_id], check=True, capture_output=True, text=True
    )
    events = [json.loads(line) for line in emitted.stdout.splitlines()]
    assert len(events) == manifest["event_count"]
    assert all(event["data_kind"] == "synthetic" for event in events)
    assert [event["event_id"] for event in events] == [
        f"{run_id}/{index}" for index in range(1, len(events) + 1)
    ]
    (shift,) = [event for event in events if event["event"] == "mss_confirmed"]
    assert shift["confirmed_at"] == (START + 50 * MINUTE).isoformat()
    assert shift["pivot"]["confirmed_at"] == (START + 35 * MINUTE).isoformat()
    config = StructureConfig(PivotRule(1, 1))
    assert store.build_structure_run(dataset_id, TIMEFRAME, config) == run_id
    alternate = StructureConfig(config.internal, config.external, BreakRule(BreakMode.WICK))
    assert store.build_structure_run(dataset_id, TIMEFRAME, alternate) != run_id
    assert list(DatasetStore(store.path).structure_events(run_id)) == events


def test_v1_sqlite_candle_reads_and_migration_preserve_existing_data(tmp_path: Path) -> None:
    store, dataset_id = prepare_synthetic_replay(tmp_path)
    metadata = store.describe(dataset_id)
    bars = list(store.replay(dataset_id))
    with sqlite3.connect(store.path) as db:
        # Remove only the empty v2 tables in this synthetic test database.
        db.execute("DROP TABLE structure_events")
        db.execute("DROP TABLE structure_runs")
        db.execute("DROP TABLE forward_report_publications")
        db.execute("DROP TABLE runtime_profiles")
        db.execute("DROP TABLE analysis_reports")
        db.execute("DROP TABLE notification_attempts")
        db.execute("DROP TABLE notification_deliveries")
        db.execute("DROP TABLE notification_cancellations")
        db.execute("DROP TABLE forward_events")
        db.execute("DROP TABLE forward_runs")
        db.execute("DROP TABLE monitor_events")
        db.execute("DROP TABLE monitor_alerts")
        db.execute("DROP TABLE monitor_market_events")
        db.execute("DROP TABLE monitor_minutes")
        db.execute("DROP TABLE monitor_runs")
        db.execute("PRAGMA user_version = 1")
    assert list(store.replay(dataset_id)) == bars
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
    config = StructureConfig(PivotRule(1, 1))
    run_id = store.build_structure_run(dataset_id, TIMEFRAME, config)
    assert store.describe_structure_run(run_id)["event_count"]
    assert list(store.replay(dataset_id)) == bars
    assert store.describe(dataset_id) == metadata
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_structure_run_rollback_on_missing_data_or_mid_event_failure(tmp_path: Path) -> None:
    config = StructureConfig(PivotRule(1, 1))
    store, dataset_id = prepare_synthetic_replay(tmp_path, with_gap=True)
    with pytest.raises(ValueError, match="expected candles are missing"):
        store.build_structure_run(dataset_id, TIMEFRAME, config)
    store, dataset_id = prepare_synthetic_replay(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute(
            """CREATE TRIGGER fail_structure BEFORE INSERT ON structure_events WHEN new.sequence = 2
               BEGIN SELECT RAISE(ABORT, 'synthetic persistence failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic persistence failure"):
        store.build_structure_run(dataset_id, TIMEFRAME, config)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM structure_runs").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM structure_events").fetchone() == (0,)


def test_simultaneous_synthetic_replays_release_the_store_until_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, dataset_id = prepare_synthetic_replay(tmp_path)
    config = StructureConfig(PivotRule(1, 1))
    at_first_bar, release = Barrier(3), Event()
    original = StructureEngine.push

    def pause(engine: StructureEngine, bar: AggregateCandle) -> tuple[StructureEvent, ...]:
        if bar.available_at == START + 5 * MINUTE:
            at_first_bar.wait(timeout=8)
            assert release.wait(timeout=8), "synthetic replay was not released"
        return original(engine, bar)

    monkeypatch.setattr(StructureEngine, "push", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store.build_structure_run, dataset_id, TIMEFRAME, config)
        second = pool.submit(store.build_structure_run, dataset_id, TIMEFRAME, config)
        try:
            at_first_bar.wait(timeout=8)
            with store.connection() as db:
                assert db.execute("SELECT count(*) FROM structure_runs").fetchone()[0] == 0
            assert store.save_analysis("synthetic test", {"test": True}, {"observations": 0})
        finally:
            release.set()
        run_id = first.result(timeout=5)
        assert second.result(timeout=5) == run_id
    events = list(store.structure_events(run_id))
    assert len(events) == store.describe_structure_run(run_id)["event_count"]
    assert len({event["event_id"] for event in events}) == len(events) > 0


def test_evidence_replay_links_only_an_earlier_sweep_to_mss(tmp_path: Path) -> None:
    store, dataset_id = prepare_synthetic_replay(tmp_path)
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    args = [*aurum, "structure", dataset_id, "--internal-pivot", "1", "1", "--evidence"]
    run = subprocess.run(args, check=True, capture_output=True, text=True)
    manifest = json.loads(run.stdout)
    run_id = manifest["run_id"]
    assert manifest["evidence_version"] == 1
    assert manifest["evidence_config"]["imbalances"]["min_width_ticks"] == 1
    events = list(store.structure_events(run_id))
    assert any(event["event"] == "liquidity_level_mapped" for event in events)
    (link,) = [event for event in events if event["event"] == "liquidity_sweep_mss_linked"]
    assert link["elapsed_bars"] == 3
    assert link["elapsed_seconds"] == 900
    assert link["confirmed_at"] == (START + 50 * MINUTE).isoformat()
    alternate = subprocess.run(
        [*args, "--min-gap-ticks", "3"], check=True, capture_output=True, text=True
    )
    assert json.loads(alternate.stdout)["run_id"] != run_id
    same = subprocess.run(args, check=True, capture_output=True, text=True)
    assert same.stdout == run.stdout


def test_historical_chart_index_matches_synthetic_full_journal_projection(tmp_path: Path) -> None:
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    bars = [
        synthetic_bar(index, 29000 + (index % 8) * 10, 29002 + (index % 8) * 10)
        for index in range(36)
    ]
    store, dataset = prepare_synthetic_replay(tmp_path, bars=bars)
    run_id = store.build_structure_run(
        dataset,
        TIMEFRAME,
        StructureConfig(PivotRule(1, 1)),
        evidence=EvidenceConfig(
            liquidity=LiquidityConfig(max_level_age_bars=3),
            imbalances=ImbalanceConfig(max_age_bars=3),
        ),
    )
    left, right = bars[31].opened_at, bars[33].available_at
    route = f"/api/runs/{run_id}/chart-evidence"
    params = {"start": left.isoformat(), "through": right.isoformat()}
    with http.TestClient(api.create_app(store)) as client:
        indexed = client.get(route, params=params)
        assert indexed.status_code == 200 and indexed.json()["spans"]
        with sqlite3.connect(store.path) as db:
            db.execute("DROP INDEX structure_event_available")
            db.execute("PRAGMA user_version = 10")
        assert client.get(route, params=params).json() == indexed.json()


@pytest.mark.parametrize("mirror", [False, True])
def test_strategy_cli_replays_complete_mirrored_setup_and_records_filters(
    tmp_path: Path, mirror: bool
) -> None:
    bars = [
        *(synthetic_bar(index, *bound, mirror=mirror) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 81, 94, 85, mirror=mirror),
        synthetic_bar(10, 78, 90, 89, mirror=mirror),
        synthetic_bar(11, 88, 118, 116, mirror=mirror),
        synthetic_bar(12, 91, 118, 110, mirror=mirror),
        synthetic_bar(13, 103, 120, 111, mirror=mirror),
    ]
    store, dataset_id = prepare_synthetic_replay(tmp_path, bars=bars)
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    args = [*aurum, "strategy", dataset_id, "--internal-pivot", "1", "1"]
    output = subprocess.run(args, check=True, capture_output=True, text=True)
    manifest = json.loads(output.stdout)
    run_id = manifest["run_id"]
    assert manifest["execution_mode"] == "paper_research"
    assert manifest["strategy_version"] == 1
    assert manifest["data_kind"] == "synthetic"
    assert manifest["economic_calendar"] is None

    observed = subprocess.run(
        [*aurum, "strategy-events", run_id], check=True, capture_output=True, text=True
    )
    events = [json.loads(line) for line in observed.stdout.splitlines()]
    (signal,) = [event for event in events if event["state"] == "paper_signal"]
    assert signal["direction"] == ("bearish" if mirror else "bullish")
    assert signal["confirmed_at"] == bars[12].available_at.isoformat()
    assert signal["sweep"]["confirmed_at"] == bars[10].available_at.isoformat()
    assert signal["mss"]["confirmed_at"] == bars[11].available_at.isoformat()
    assert signal["zone"]["confirmed_at"] == bars[11].available_at.isoformat()
    assert signal["plan"]["stop_ticks"] == (223 if mirror else 77)
    assert signal["plan"]["quantity"] == 1
    assert signal["plan"]["gross_risk_cents"] == 3_300
    assert signal["plan"]["net_loss_cents"] == 3_560
    assert signal["context"]["economic"]["risk"] == "unknown"
    assert signal["score"]["validated_comparable_samples"] == 0
    assert signal["cautions"] == [
        "economic_event_risk_unknown",
        "higher_timeframe_history_incomplete",
    ]
    assert [event["state"] for event in events if event["setup_id"] == signal["setup_id"]] == [
        "context_identified",
        "liquidity_event",
        "mss_confirmed",
        "zone_identified",
        "retest_pending",
        "paper_signal",
    ]
    repeated = subprocess.run(args, check=True, capture_output=True, text=True)
    assert repeated.stdout == output.stdout

    provenance = store.describe(dataset_id)["provenance"]
    replay = StrategyReplay(
        SessionCalendar.from_dict(provenance["calendar"]),
        CONTRACT,
        TIMEFRAME,
        StructureConfig(PivotRule(1, 1)),
    )
    minutes = list(store.replay(dataset_id))
    prefix = list(replay.consume(minutes[:60]))
    assert not any(event.state is SetupState.PAPER_SIGNAL for event in prefix)
    suffix = list(replay.consume(minutes[60:]))
    resumed = [event for event in suffix if event.state is SetupState.PAPER_SIGNAL]
    assert [event.setup_id for event in resumed] == [signal["setup_id"]]

    filtered = subprocess.run(
        [*args, "--variant", "sweep_mss_ifvg_htf", "--require-clear-event-risk"],
        check=True,
        capture_output=True,
        text=True,
    )
    alternative = json.loads(filtered.stdout)["run_id"]
    assert alternative != run_id
    transitions = list(store.structure_events(alternative))
    assert not any(event["state"] == "paper_signal" for event in transitions)
    assert any(
        event["failed"] == ["higher_timeframe_alignment", "event_filter"] for event in transitions
    )


def test_backtest_cli_uses_paper_signal_and_persists_trade_and_audit(tmp_path: Path) -> None:
    bars = [
        *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 81, 94, 85),
        synthetic_bar(10, 78, 90, 89),
        synthetic_bar(11, 88, 118, 116),
        synthetic_bar(12, 91, 118, 110),
        synthetic_bar(13, 70, 180, 111, 110),
    ]
    store, dataset = prepare_synthetic_replay(tmp_path, bars=bars)
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    args = [*aurum, "backtest", dataset, "--internal-pivot", "1", "1"]
    output = subprocess.run(args, check=True, capture_output=True, text=True)
    run = json.loads(output.stdout)
    assert run["data_kind"] == "synthetic"
    assert run["summary"]["signal_count"] == 1
    assert run["summary"]["performance"]["trades"] == 1
    assert run["summary"]["performance"]["net_profit_cents"] == -3560
    assert run["summary"]["performance"]["ambiguous_exits"] == 1
    events = list(store.structure_events(run["run_id"]))
    (trade,) = [event for event in events if event["event"] == "paper_trade_completed"]
    (signal,) = [event for event in events if event.get("state") == "paper_signal"]
    assert trade["setup_id"] == signal["setup_id"]
    assert trade["entry_at"] == signal["confirmed_at"] == bars[13].opened_at.isoformat()
    assert trade["entry_ticks"] == 110
    assert trade["exit_ticks"] == signal["stop_ticks"] == 77
    assert trade["recorded_at"] == (bars[13].opened_at + MINUTE).isoformat()
    assert trade["gross_cents"] == -3300
    analytics = backtest_analytics(store, run["run_id"], window=5)
    assert analytics["completed_trades"] == analytics["signal_count"] == 1
    assert analytics["signals_by_utc_date"] == {"2025-03-10": 1}
    assert analytics["signals_by_session"] == {"new_york": 1}
    assert analytics["scored_trades"] == 1
    costs = analytics["cost_sensitivity_fixed_fills"]
    assert isinstance(costs, list)
    assert costs[1]["net_cents"] == int(str(trade["net_cents"])) - 50 * int(str(trade["quantity"]))
    repeated = subprocess.run(args, check=True, capture_output=True, text=True)
    assert repeated.stdout == output.stdout
    report = subprocess.run(
        [
            *aurum,
            "validate",
            run["run_id"],
            "--validation-start",
            "2025-03-10T13:30Z",
            "--holdout-start",
            "2025-03-10T14:07Z",
            "--minimum-selection-trades",
            "1",
            "--bootstrap-draws",
            "10",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    validation = json.loads(report.stdout)
    assert validation["result"]["data_kind"] == "synthetic"
    assert validation["result"]["selected_run_id"] == run["run_id"]
    assert validation["result"]["candidates"][run["run_id"]]["validation"]["net_cents"] == -3560
    assert validation["result"]["holdout"]["summary"]["trades"] == 0


def test_matched_control_fits_only_development_and_replays_heldout_fills(tmp_path: Path) -> None:
    # Five fictional sessions. The second develops an MSS without an IFVG retest.
    bars = [
        *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 81, 94, 85),
        synthetic_bar(10, 78, 90, 89),
        synthetic_bar(11, 88, 118, 116),
        synthetic_bar(12, 91, 118, 110),
        synthetic_bar(13, 70, 180, 111, 110),
    ]
    source = tmp_path / "synthetic-control.csv"
    rows = ["timestamp,contract,open,high,low,close,volume"]
    sessions = []
    for index in range(5):
        offset = timedelta(days=index)
        window = TradingWindow(bars[0].opened_at + offset, bars[-1].available_at + offset)
        sessions.append(TradingSession(TRADE_DATE + offset, (window,)))
        day = bars
        if index == 1:
            day = [*bars[:12], synthetic_bar(12, 119, 125), synthetic_bar(13, 123, 128)]
        elif index == 4:
            day = [*bars[:12], synthetic_bar(12, 119, 125, 123), bars[13]]
        for bar in day:
            for minute in range(5):
                opened = bar.opened_at + offset + minute * MINUTE
                rows.append(
                    f"{opened.isoformat()},{CONTRACT.code},{bar.open_ticks / 10:.1f},"
                    f"{bar.high_ticks / 10:.1f},{bar.low_ticks / 10:.1f},"
                    f"{bar.close_ticks / 10:.1f},2"
                )
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    calendar = SessionCalendar(
        "synthetic control calendar",
        "fixture-v1",
        TradingWindow(sessions[0].start, sessions[-1].end),
        sessions=tuple(sessions),
    )
    store = DatasetStore(tmp_path / "synthetic-control.sqlite")
    config = ImportConfig("synthetic", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC)
    dataset = store.save(prepare_csv(source, config, calendar))

    def run(variant: Variant, pivot: int = 1) -> str:
        return store.build_strategy_run(
            dataset,
            TIMEFRAME,
            StructureConfig(PivotRule(pivot, pivot)),
            replace(DEFAULT_STRATEGY_CONFIG, variant=variant),
            execution=DEFAULT_EXECUTION_CONFIG,
        )

    reference, opportunity = run(Variant.SWEEP_MSS_IFVG), run(Variant.MSS)
    validation_config = ValidationConfig(
        sessions[2].start - timedelta(hours=2),
        sessions[3].start,
        minimum_selection_trades=1,
        bootstrap_draws=10,
    )
    validation = validate_runs(store, (reference,), validation_config)
    args = [
        str(Path(executable).with_name("aurum")),
        "--db",
        str(store.path),
        "matched-control",
        validation,
        opportunity,
        "--replications",
        "30",
        "--bootstrap-draws",
        "10",
    ]
    output = subprocess.run(args, check=True, capture_output=True, text=True)
    report = json.loads(output.stdout)
    result = report["result"]
    assert result["data_kind"] == "synthetic"
    assert result["holdout_used_for_calibration"] is False
    assert result["fit"] == [
        {
            "admission_rate": 0.5,
            "development_opportunities": 2,
            "development_reference_signals": 1,
            "reference_rate_unattainable": False,
            "stratum": ["new_york", "bullish"],
        }
    ]
    assert result["holdout_reference_opportunities"] == 1
    assert result["holdout_control_opportunities"] == 2
    assert result["reference"]["net_profit_cents"] == -160
    assert {arm["admitted_signals"] for arm in result["controls"]} == {0, 1, 2}
    assert result["control_net_distribution_cents"]["replications"] == 30
    assert result["control_expectancy_distribution_cents"]["replications"] < 30
    assert result["audit"]["reference"]["signals"][0]["decision_at"].startswith("2025-03-13")
    assert result["audit"]["reference"]["events"][-1]["net_cents"] == -160

    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    with http.TestClient(api.create_app(store)) as client:
        request = {
            "validation_id": validation,
            "opportunity_run_id": opportunity,
            "bootstrap_draws": 10,
        }
        saved = client.post("/api/reports/matched-control", json=request)
        assert saved.status_code == 201
        assert saved.json() == report
        oversized = client.post(
            "/api/reports/matched-control",
            json={
                **request,
                "replications": 101,
            },
        )
        assert oversized.status_code == 422

    with pytest.raises(ValueError, match="distinct"):
        save_matched_control(store, validation, reference)
    incompatible = run(Variant.MSS, pivot=2)
    with pytest.raises(ValueError, match="share data, rules, risk and execution"):
        save_matched_control(store, validation, incompatible)
    moved = validate_runs(
        store,
        (reference,),
        replace(
            validation_config,
            minimum_selection_trades=100,
        ),
    )
    assert store.describe_analysis(moved)["kind"] == "chronological_validation"
    with pytest.raises(ValueError, match="selected independent holdout"):
        save_matched_control(store, moved, opportunity)

    embargoed = validate_runs(store, (reference,), replace(validation_config, embargo_minutes=61))
    embargoed_report = store.describe_analysis(
        save_matched_control(store, embargoed, opportunity, ControlConfig(bootstrap_draws=10))
    )
    definition, embargoed_result = embargoed_report["definition"], embargoed_report["result"]
    assert isinstance(definition, dict) and isinstance(embargoed_result, dict)
    assert definition["holdout_start"] == (sessions[3].start + 61 * MINUTE).isoformat()
    assert embargoed_result["fit"] == result["fit"]
    assert embargoed_result["holdout_reference_opportunities"] == 1
    assert embargoed_result["holdout_control_opportunities"] == 1

    shifted = validate_runs(
        store,
        (reference,),
        replace(
            validation_config,
            holdout_start=sessions[4].start,
        ),
    )
    shifted_result = store.describe_analysis(
        save_matched_control(store, shifted, opportunity, ControlConfig(bootstrap_draws=10))
    )["result"]
    assert isinstance(shifted_result, dict)
    assert shifted_result["fit"] == result["fit"]
    assert shifted_result["holdout_control_opportunities"] == 1


def test_persistent_monitor_replay_matches_strategy_and_gates_synthetic_alerts(
    tmp_path: Path,
) -> None:
    from aurum.evidence import EvidenceEngine
    from aurum.feed_frames import FeedFrame
    from aurum.monitoring import (
        MarketMonitor,
        MonitorConfig,
        market_events_page,
        monitor_alerts,
        monitor_events,
    )
    from aurum.providers import FeedStatus
    from aurum.strategy import DEFAULT_STRATEGY_CONFIG

    bars = [
        *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 81, 94, 85),
        synthetic_bar(10, 78, 90, 89),
        synthetic_bar(11, 88, 118, 116),
        synthetic_bar(12, 91, 118, 110),
        synthetic_bar(13, 103, 120, 111),
    ]
    store, dataset = prepare_synthetic_replay(tmp_path, bars=bars)
    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    args = [*aurum, "monitor-replay", dataset, "--internal-pivot", "1", "1"]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    monitor_id = json.loads(result.stdout)["monitor_id"]
    observed = [
        item
        for item in monitor_events(store, monitor_id)
        if item["event"] == "paper_setup_transition"
    ]
    structure = StructureConfig(PivotRule(1, 1))
    calendar = SessionCalendar.from_dict(store.describe(dataset)["provenance"]["calendar"])
    replay = StrategyReplay(calendar, CONTRACT, TIMEFRAME, structure)
    expected = [
        json.loads(json.dumps(event.as_dict())) for event in replay.consume(store.replay(dataset))
    ]
    assert len(observed) == len(expected)
    for item, event in zip(observed, expected, strict=True):
        assert {key: item[key] for key in event} == event
        assert item["source_status"] == "simulated"
        assert item["alert_eligible"] is False
    assert list(monitor_alerts(store, monitor_id)) == []
    recorded_market = market_events_page(store, monitor_id, limit=500)
    expected_market = EvidenceEngine(calendar, CONTRACT, TIMEFRAME, structure).consume(
        store.replay(dataset)
    )
    assert [
        {
            key: value
            for key, value in item.items()
            if key
            not in ("sequence", "event_id", "source_status", "source_received_at", "observed_at")
        }
        for item in recorded_market
        if item["event"] != "market_context_observed"
    ] == [event.as_dict() for event in expected_market]
    assert subprocess.run(args, check=True, capture_output=True, text=True).stdout == result.stdout
    assert market_events_page(store, monitor_id, limit=500) == recorded_market

    live_config = MonitorConfig(
        "synthetic fixture classified real-time to test alert gate", FeedStatus.REAL_TIME
    )
    instance = MarketMonitor(
        store, calendar, CONTRACT, live_config, structure, DEFAULT_STRATEGY_CONFIG
    )
    instance.set_connection(True, bars[0].opened_at)
    minutes = list(store.replay(dataset))
    for minute in minutes:
        instance.ingest(FeedFrame.from_replay(minute, FeedStatus.REAL_TIME, minute.available_at))
    (alert,) = list(monitor_alerts(store, instance.monitor_id))
    assert alert["execution_mode"] == "paper_research"
    assert alert["data_status"] == "real_time"
    assert alert["feed_age_seconds"] == 0
    last = minutes[-1]
    instance.ingest(FeedFrame.from_replay(last, FeedStatus.REAL_TIME, last.available_at))
    assert len(list(monitor_alerts(store, instance.monitor_id))) == 1


@pytest.mark.parametrize("published_early", [False, True])
def test_monitor_restarts_with_point_in_time_event_filter_and_matches_strategy(
    tmp_path: Path, published_early: bool
) -> None:
    bars = [
        *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 81, 94, 85),
        synthetic_bar(10, 78, 90, 89),
        synthetic_bar(11, 88, 118, 116),
        synthetic_bar(12, 91, 118, 110),
        synthetic_bar(13, 103, 120, 111),
    ]
    store, dataset = prepare_synthetic_replay(tmp_path, bars=bars)
    decision = bars[12].available_at
    snapshot = EconomicCalendar(
        "synthetic scheduled release fixture",
        "fixture-v1",
        TradingWindow(START - 30 * MINUTE, decision + 30 * MINUTE),
        START - 60 * MINUTE,
        (
            Announcement(
                "fictional-announcement",
                "Synthetic release",
                decision + 2 * MINUTE,
                decision + (-2 if published_early else 1) * MINUTE,
            ),
        ),
    )
    path = tmp_path / "synthetic-economic.json"
    path.write_text(json.dumps(snapshot.as_dict()), encoding="utf-8")
    calendar = SessionCalendar.from_dict(store.describe(dataset)["provenance"]["calendar"])
    config = MonitorConfig(f"replay:{dataset}", FeedStatus.SIMULATED)
    structure = StructureConfig(PivotRule(1, 1))
    strategy = replace(DEFAULT_STRATEGY_CONFIG, require_clear_event_risk=True)
    instance = MarketMonitor(
        store, calendar, CONTRACT, config, structure, strategy, economic=snapshot
    )
    without_snapshot = MarketMonitor(store, calendar, CONTRACT, config, structure, strategy)
    assert without_snapshot.monitor_id != instance.monitor_id
    instance.set_connection(True, START)
    for minute in list(store.replay(dataset))[:62]:
        instance.ingest(FeedFrame.from_replay(minute, FeedStatus.SIMULATED, minute.available_at))

    aurum = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    options = [
        dataset,
        "--internal-pivot",
        "1",
        "1",
        "--economic-calendar",
        str(path),
        "--require-clear-event-risk",
    ]
    run = json.loads(
        subprocess.run(
            [*aurum, "monitor-replay", *options],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert run["monitor_id"] == instance.monitor_id
    assert run["definition"]["economic_calendar"] == snapshot.as_dict()
    transitions = [
        event
        for event in monitor_events(store, instance.monitor_id)
        if event["event"] == "paper_setup_transition"
    ]
    historical = json.loads(
        subprocess.run(
            [*aurum, "strategy", *options],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    for observed, expected in zip(
        transitions, store.structure_events(historical["run_id"]), strict=True
    ):
        shared = (observed.keys() & expected.keys()) - {"event_id"}
        assert {key: observed[key] for key in shared} == {key: expected[key] for key in shared}
    assert any(event["state"] == "paper_signal" for event in transitions) is not published_early
    contexts = market_events_page(store, instance.monitor_id, kind="market_context_observed")
    risks = {}
    for event in contexts:
        context = event["context"]
        assert isinstance(context, dict)
        risks[event["confirmed_at"]] = context["economic"]["risk"]
    assert risks[(decision - 5 * MINUTE).isoformat()] == "clear"
    assert risks[decision.isoformat()] == ("elevated" if published_early else "clear")
    assert risks[(decision + 5 * MINUTE).isoformat()] == "elevated"


@pytest.mark.parametrize("recover", [False, True])
def test_forward_paper_restores_after_observed_signal_and_handles_recovery(
    tmp_path: Path, recover: bool
) -> None:
    bars = [
        *(synthetic_bar(index, *bound) for index, bound in enumerate(BEARISH)),
        synthetic_bar(9, 81, 94, 85),
        synthetic_bar(10, 78, 90, 89),
        synthetic_bar(11, 88, 118, 116),
        synthetic_bar(12, 91, 118, 110),
        synthetic_bar(13, 102, 122, 111, 110),
        synthetic_bar(14, 105, 190, 181, 110),
    ]
    store, dataset = prepare_synthetic_replay(tmp_path, bars=bars)
    calendar = SessionCalendar.from_dict(store.describe(dataset)["provenance"]["calendar"])
    monitor = MarketMonitor(
        store,
        calendar,
        CONTRACT,
        MonitorConfig("synthetic forward verification", FeedStatus.SIMULATED),
        StructureConfig(PivotRule(1, 1)),
        DEFAULT_STRATEGY_CONFIG,
    )
    with pytest.raises(ValueError, match="configured as real_time"):
        register_forward(store, monitor.monitor_id, START)
    run = register_forward(store, monitor.monitor_id, START, synthetic=True)
    monitor.set_connection(True, START)
    minutes = list(store.replay(dataset))
    receipt_offset = timedelta(seconds=2)
    for candle in minutes[:65]:
        monitor.ingest(
            FeedFrame.from_replay(
                candle, FeedStatus.SIMULATED, candle.available_at + receipt_offset
            )
        )
    paper = ForwardPaper(store, run)
    queued = paper.sync()
    assert queued["caught_up"] is True and queued["pending_setup_id"]
    signal, order = forward_events(store, run)
    assert signal["data_kind"] == "synthetic_forward_verification"
    assert signal["event"] == "paper_forward_signal"
    assert order["recorded_at"] == (minutes[64].available_at + receipt_offset).isoformat()
    for candle in minutes[65:67]:
        monitor.ingest(
            FeedFrame.from_replay(
                candle, FeedStatus.SIMULATED, candle.available_at + receipt_offset
            )
        )
    restarted = ForwardPaper(store, run)
    filled = restarted.sync()
    assert filled["open_setup_id"] == queued["pending_setup_id"]
    fill = forward_events(store, run)[-1]
    assert fill["entry_at"] == minutes[66].opened_at.isoformat()
    assert fill["recorded_at"] == (minutes[66].available_at + receipt_offset).isoformat()

    if recover:
        receipt = minutes[68].available_at + receipt_offset
        monitor.ingest(FeedFrame.from_replay(minutes[68], FeedStatus.SIMULATED, receipt))
        assert restarted.sync()["open_setup_id"] == filled["open_setup_id"]
        monitor.ingest(FeedFrame.from_replay(minutes[67], FeedStatus.SIMULATED, receipt))
    else:
        target = replace(minutes[67], high_ticks=190)
        receipt = target.available_at + receipt_offset
        monitor.ingest(FeedFrame.from_replay(target, FeedStatus.SIMULATED, receipt))

    with sqlite3.connect(store.path) as db:
        db.execute(
            """CREATE TRIGGER fail_forward BEFORE INSERT ON forward_events
               BEGIN SELECT RAISE(ABORT, 'synthetic forward persistence failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic forward persistence failure"):
        restarted.sync()
    assert forward_events(store, run)[-1] == fill
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER fail_forward")
    assert restarted.sync()["open_setup_id"] is None
    outcome = forward_events(store, run)[-1]
    assert outcome["recorded_at"] == receipt.isoformat()
    if recover:
        assert outcome["event"] == "paper_trade_unresolved"
        assert "net_cents" not in outcome
    else:
        assert outcome["event"] == "paper_trade_completed"
        assert outcome["reason"] == "target"
    assert ForwardPaper(store, run).sync()["processed_minutes"] == 0
    cli = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    audited = subprocess.run(
        [*cli, "forward-events", run], check=True, capture_output=True, text=True
    )
    assert [json.loads(line) for line in audited.stdout.splitlines()] == forward_events(store, run)
    late = register_forward(store, monitor.monitor_id, receipt, synthetic=True)
    assert ForwardPaper(store, late).sync()["processed_minutes"] == 0
    assert forward_events(store, late) == []

    for period in ForwardPeriod:
        report_id = save_forward_report(store, run, period, TRADE_DATE, bootstrap_draws=10)
        assert save_forward_report(store, run, period, TRADE_DATE, bootstrap_draws=10) == report_id
        report = store.describe_analysis(report_id)
        result = report["result"]
        assert isinstance(result, dict)
        assert result["data_kind"] == "synthetic_forward_verification"
        sample = result["period"]
        assert sample["qualified_signals"] == sample["entries_filled"] == 1
        assert sample["outcomes"]["trades"] == (0 if recover else 1)
        assert sample["unresolved_outcomes"] == int(recover)
        assert not sample["coverage"]["all_reviewed_minutes_applied"]
        assert result["prior_period_change"]["difference"]["expectancy_cents"] is None
    if not recover:
        history = store.build_strategy_run(
            dataset,
            TIMEFRAME,
            StructureConfig(PivotRule(1, 1)),
            DEFAULT_STRATEGY_CONFIG,
            execution=DEFAULT_EXECUTION_CONFIG,
        )
        baseline = validate_runs(
            store,
            (history,),
            ValidationConfig(
                START + 55 * MINUTE,
                START + 72 * MINUTE,
                minimum_selection_trades=1,
                bootstrap_draws=10,
            ),
        )
        with pytest.raises(ValueError, match="history ending before registration"):
            save_forward_report(
                store, run, ForwardPeriod.DAILY, TRADE_DATE, baseline_validation_id=baseline
            )
        later = register_forward(store, monitor.monitor_id, datetime.now(UTC), synthetic=True)
        compared = save_forward_report(
            store, later, ForwardPeriod.DAILY, TRADE_DATE, baseline_validation_id=baseline
        )
        comparison = store.describe_analysis(compared)["result"]
        assert isinstance(comparison, dict)
        assert comparison["historical_holdout"]["used_for_selection"] is False
        assert comparison["historical_holdout"]["backtest_run_id"] == history
