import json
import sqlite3
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from sys import executable
from threading import Barrier, Event

import pytest

from aurum.candles import AggregateCandle, Timeframe
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
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
from aurum.storage import SCHEMA_VERSION, DatasetStore
from aurum.strategy import SetupState, StrategyReplay
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
        db.execute("DROP TABLE analysis_reports")
        db.execute("DROP TABLE notification_attempts")
        db.execute("DROP TABLE notification_deliveries")
        db.execute("DROP TABLE notification_cancellations")
        db.execute("DROP TABLE monitor_events")
        db.execute("DROP TABLE monitor_alerts")
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


def test_persistent_monitor_replay_matches_strategy_and_gates_synthetic_alerts(
    tmp_path: Path,
) -> None:
    from aurum.feed_frames import FeedFrame
    from aurum.monitoring import MarketMonitor, MonitorConfig, monitor_alerts, monitor_events
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
    assert subprocess.run(args, check=True, capture_output=True, text=True).stdout == result.stdout

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
