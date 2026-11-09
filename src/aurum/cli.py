import csv
import signal
import sqlite3
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from aurum.account_reports import simulate_account_ledger
from aurum.account_simulation import LUCIDFLEX_25K, AccountPhase, BreachMetric
from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.continuous import SeriesPlan, replay_series, save_series
from aurum.contracts import CONTRACT_VERSION, ContractCatalog, RollPolicy
from aurum.economic_events import EconomicCalendar
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EvidenceConfig
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.execution import DEFAULT_EXECUTION_CONFIG, ExecutionConfig
from aurum.feed_frames import FeedFrame
from aurum.instruments import MgcContract
from aurum.market_context import Regime, ResearchSession
from aurum.market_data import (
    DataKind,
    ImportConfig,
    TimestampLabel,
    canonical_json,
    prepare_history,
)
from aurum.market_structure import (
    DEFAULT_STRUCTURE_CONFIG,
    BreakMode,
    BreakRule,
    PivotRule,
    StructureConfig,
)
from aurum.monitoring import (
    MarketMonitor,
    MonitorConfig,
    describe_monitor,
    monitor_alerts,
    monitor_events,
)
from aurum.monte_carlo import (
    REFERENCE_WIN_RATES_BPS,
    MonteCarloConfig,
    SamplingMode,
    TrailingLossScenario,
    WinRateScenario,
)
from aurum.paper_risk import DEFAULT_RISK_CONFIG, TargetMode
from aurum.providers import FeedStatus, FileHistorySource, HttpsHistorySource, SourceFormat
from aurum.research_reports import simulate_report, validate_runs
from aurum.statistics import ValidationConfig
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, Variant
from aurum.streaming import HttpsGapBackfill, WebSocketSource, run_stream
from aurum.timestamps import minute_timestamp


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(prog="aurum", description="MGC historical research data")
    parser.add_argument("--db", type=Path, required=True, help="SQLite dataset store")
    commands = parser.add_subparsers(dest="command", required=True)

    importer = commands.add_parser("import-csv", help="validate and store unadjusted MGC candles")
    importer.add_argument("csv", type=Path)
    _import_options(importer)
    parquet = commands.add_parser("import-parquet", help="validate and store Parquet MGC candles")
    parquet.add_argument("parquet", type=Path)
    _import_options(parquet)
    historical = commands.add_parser(
        "import-https", help="import from an authorized normalized endpoint"
    )
    historical.add_argument("url")
    historical.add_argument(
        "--format", type=SourceFormat, choices=SourceFormat, default=SourceFormat.CSV
    )
    historical.add_argument("--token-env")
    _import_options(historical)
    catalog = commands.add_parser("contracts", help="assess a reviewed MGC contract inventory")
    catalog.add_argument("catalog", type=Path)
    catalog.add_argument("--as-of", type=minute_timestamp, required=True)
    catalog.add_argument("--current", type=MgcContract)
    catalog.add_argument("--expiry-lead-days", type=int, default=3)
    catalog.add_argument("--volume-lead-bps", type=int, default=10_000)
    catalog.add_argument("--open-interest-lead-bps", type=int)
    continuous = commands.add_parser(
        "continuous", help="save an explicit continuous research series"
    )
    continuous.add_argument("plan", type=Path)
    continuous_bars = commands.add_parser(
        "continuous-bars", help="stream a saved continuous research series"
    )
    continuous_bars.add_argument("report_id")

    inspect = commands.add_parser("inspect", help="show provenance and quality findings")
    inspect.add_argument("dataset_id")
    replay = commands.add_parser("replay", help="emit chronological candle-close events as JSONL")
    replay.add_argument("dataset_id")
    aggregate = commands.add_parser("aggregate", help="emit completed higher-timeframe candles")
    aggregate.add_argument("dataset_id")
    aggregate.add_argument("--timeframe", type=Timeframe, choices=Timeframe, action="append")
    structure = commands.add_parser("structure", help="persist a versioned structure replay")
    _research_options(structure)
    structure.add_argument(
        "--evidence", action="store_true", help="include liquidity and gap events"
    )
    strategy = commands.add_parser("strategy", help="persist paper setup transitions")
    _strategy_options(strategy)
    backtest = commands.add_parser("backtest", help="run a minute-level paper simulation")
    _strategy_options(backtest)
    backtest.add_argument(
        "--latency-minutes", type=int, default=DEFAULT_EXECUTION_CONFIG.latency_minutes
    )
    backtest.add_argument(
        "--entry-timeout-minutes", type=int, default=DEFAULT_EXECUTION_CONFIG.entry_timeout_minutes
    )
    backtest.add_argument(
        "--round-trip-spread-ticks",
        type=int,
        default=DEFAULT_EXECUTION_CONFIG.round_trip_spread_ticks,
    )
    backtest.add_argument("--carry-across-closures", action="store_true")
    backtest.add_argument(
        "--initial-equity-cents", type=int, default=DEFAULT_EXECUTION_CONFIG.initial_equity_cents
    )
    structure_events = commands.add_parser(
        "structure-events", help="read persisted events as JSONL"
    )
    structure_events.add_argument("run_id")
    strategy_events = commands.add_parser(
        "strategy-events", help="read persisted paper transitions"
    )
    strategy_events.add_argument("run_id")
    backtest_events = commands.add_parser(
        "backtest-events", help="read simulated fills and audit events"
    )
    backtest_events.add_argument("run_id")
    backtest_summary = commands.add_parser(
        "backtest-summary", help="read a persisted paper backtest summary"
    )
    backtest_summary.add_argument("run_id")
    validation = commands.add_parser("validate", help="compare chronological backtest samples")
    validation.add_argument("run_ids", nargs="+")
    validation.add_argument("--validation-start", type=minute_timestamp, required=True)
    validation.add_argument("--holdout-start", type=minute_timestamp, required=True)
    validation.add_argument("--embargo-minutes", type=int, default=0)
    validation.add_argument("--minimum-selection-trades", type=int, default=20)
    validation.add_argument("--bootstrap-draws", type=int, default=2_000)
    validation.add_argument("--block-length", type=int, default=5)
    validation.add_argument("--seed", type=int, default=42)
    validation.add_argument("--walk-train-days", type=int, default=90)
    validation.add_argument("--walk-test-days", type=int, default=30)
    validation.add_argument("--extra-cost-cents", type=int, action="append")
    monte_carlo = commands.add_parser("monte-carlo", help="simulate conditional equity paths")
    monte_carlo.add_argument(
        "--mode", type=SamplingMode, choices=SamplingMode, default=SamplingMode.SCENARIO
    )
    monte_carlo.add_argument("--backtest")
    monte_carlo.add_argument("--paths", type=int, default=1_000)
    monte_carlo.add_argument("--trades", type=int, default=1_000)
    monte_carlo.add_argument("--seed", type=int, default=42)
    monte_carlo.add_argument("--block-length", type=int, default=5)
    monte_carlo.add_argument("--initial-equity-cents", type=int, default=2_500_000)
    monte_carlo.add_argument("--loss-allowance-cents", type=int, default=100_000)
    monte_carlo.add_argument("--win-rate-bps", type=int, action="append")
    monte_carlo.add_argument("--gross-win-cents", type=int)
    monte_carlo.add_argument("--gross-loss-cents", type=int)
    monte_carlo.add_argument("--minimum-cost-cents", type=int)
    monte_carlo.add_argument("--maximum-cost-cents", type=int)
    monte_carlo.add_argument("--lucidflex-trades-per-session", type=int)
    report = commands.add_parser("report", help="read a persisted analysis report")
    report.add_argument("report_id")
    account = commands.add_parser("account", help="simulate a LucidFlex paper account ledger")
    account.add_argument("ledger", type=Path)
    account.add_argument("--calendar", type=Path, required=True)
    account.add_argument(
        "--phase", type=AccountPhase, choices=AccountPhase, default=AccountPhase.EVALUATION
    )
    account.add_argument("--consistency-bps", type=int, choices=(4_000, 5_000), default=4_000)
    account.add_argument(
        "--breach-metric", type=BreachMetric, choices=BreachMetric, default=BreachMetric.EQUITY
    )
    account.add_argument("--daily-loss-cents", type=int)
    account.add_argument("--allow-intraday-payouts", action="store_true")

    monitor = commands.add_parser("monitor", help="consume an authorized normalized WebSocket feed")
    _research_options(monitor, dataset_argument=False)
    monitor.add_argument("endpoint")
    monitor.add_argument("--calendar", type=Path, required=True)
    monitor.add_argument("--contract", type=MgcContract, required=True)
    monitor.add_argument("--source", required=True)
    monitor.add_argument(
        "--feed-status",
        type=FeedStatus,
        choices=(FeedStatus.REAL_TIME, FeedStatus.DELAYED, FeedStatus.SIMULATED),
        required=True,
    )
    monitor.add_argument("--token-env")
    monitor.add_argument("--backfill-url")
    monitor.add_argument("--backfill-token-env")
    monitor.add_argument("--stale-seconds", type=int, default=90)
    monitor.add_argument("--max-frames", type=int)
    monitor.add_argument(
        "--variant", type=Variant, choices=Variant, default=DEFAULT_STRATEGY_CONFIG.variant
    )
    monitor_replay = commands.add_parser(
        "monitor-replay", help="exercise the monitor with a saved dataset"
    )
    _research_options(monitor_replay)
    monitor_replay.add_argument(
        "--variant", type=Variant, choices=Variant, default=DEFAULT_STRATEGY_CONFIG.variant
    )
    for name in ("monitor-status", "monitor-events", "monitor-alerts"):
        command = commands.add_parser(name)
        command.add_argument("monitor_id")

    args = parser.parse_args(argv)
    store = DatasetStore(args.db)
    try:
        if args.command in ("import-csv", "import-parquet", "import-https"):
            print(canonical_json(store.describe(_import_history(store, args))))
        elif args.command == "contracts":
            inventory = ContractCatalog.from_json(args.catalog.read_text(encoding="utf-8"))
            policy = RollPolicy(
                args.expiry_lead_days, args.volume_lead_bps, args.open_interest_lead_bps
            )
            decision = inventory.decide(args.as_of, args.current, policy)
            report_id = store.save_analysis(
                "contract_roll_assessment",
                {
                    "contract_version": CONTRACT_VERSION,
                    "catalog": inventory.as_dict(),
                    "policy": asdict(policy),
                    "as_of": args.as_of.isoformat(),
                    "current": args.current.code if args.current else None,
                },
                {"data_kind": inventory.data_kind, **decision.as_dict()},
            )
            print(canonical_json(store.describe_analysis(report_id)))
        elif args.command == "continuous":
            plan = SeriesPlan.from_json(args.plan.read_text(encoding="utf-8"))
            print(canonical_json(store.describe_analysis(save_series(store, plan))))
        elif args.command == "continuous-bars":
            for event in replay_series(store, args.report_id):
                print(canonical_json(event))
        elif args.command == "monitor":
            print(canonical_json(_monitor_live(store, args)))
        elif args.command == "monitor-replay":
            print(canonical_json(_monitor_replay(store, args)))
        elif args.command == "monitor-status":
            print(canonical_json(describe_monitor(store, args.monitor_id)))
        elif args.command in ("monitor-events", "monitor-alerts"):
            reader = monitor_events if args.command == "monitor-events" else monitor_alerts
            for event in reader(store, args.monitor_id):
                print(canonical_json(event))
        elif args.command == "inspect":
            print(canonical_json(store.describe(args.dataset_id)))
        elif args.command == "structure":
            evidence = _evidence_config(args) if args.evidence else None
            if evidence is None and (
                args.min_gap_ticks is not None or args.inversion_closes is not None
            ):
                raise ValueError("gap settings require --evidence")
            run = store.build_structure_run(
                args.dataset_id, args.timeframe, _structure_config(args), evidence=evidence
            )
            print(canonical_json(store.describe_structure_run(run)))
        elif args.command == "strategy":
            print(canonical_json(store.describe_structure_run(_strategy_run(store, args))))
        elif args.command == "backtest":
            execution = ExecutionConfig(
                latency_minutes=args.latency_minutes,
                entry_timeout_minutes=args.entry_timeout_minutes,
                round_trip_spread_ticks=args.round_trip_spread_ticks,
                carry_across_closures=args.carry_across_closures,
                initial_equity_cents=args.initial_equity_cents,
            )
            print(canonical_json(store.describe_backtest(_strategy_run(store, args, execution))))
        elif args.command == "backtest-summary":
            print(canonical_json(store.describe_backtest(args.run_id)))
        elif args.command == "validate":
            validation_config = ValidationConfig(
                validation_start=args.validation_start,
                holdout_start=args.holdout_start,
                embargo_minutes=args.embargo_minutes,
                minimum_selection_trades=args.minimum_selection_trades,
                bootstrap_draws=args.bootstrap_draws,
                block_length=args.block_length,
                seed=args.seed,
                walk_train_days=args.walk_train_days,
                walk_test_days=args.walk_test_days,
                extra_cost_per_contract_cents=tuple(args.extra_cost_cents or (0, 50, 100, 200)),
            )
            report_id = validate_runs(store, tuple(args.run_ids), validation_config)
            print(canonical_json(store.describe_analysis(report_id)))
        elif args.command == "monte-carlo":
            print(canonical_json(store.describe_analysis(_monte_carlo(store, args))))
        elif args.command == "report":
            print(canonical_json(store.describe_analysis(args.report_id)))
        elif args.command == "account":
            calendar = SessionCalendar.from_json(args.calendar.read_text(encoding="utf-8"))
            rules = replace(
                LUCIDFLEX_25K,
                evaluation_consistency_bps=args.consistency_bps,
                breach_metric=args.breach_metric,
                daily_loss_cents=args.daily_loss_cents,
                allow_intraday_payouts=args.allow_intraday_payouts,
            )
            account_report = simulate_account_ledger(
                store, args.ledger, calendar, args.phase, rules
            )
            print(canonical_json(store.describe_analysis(account_report)))
        elif args.command in ("structure-events", "strategy-events", "backtest-events"):
            if (
                args.command == "backtest-events"
                and "backtest_version" not in store.describe_structure_run(args.run_id)
            ):
                raise ValueError("run does not contain a paper backtest")
            if (
                args.command == "strategy-events"
                and "strategy_version" not in store.describe_structure_run(args.run_id)
            ):
                raise ValueError("run does not contain paper strategy transitions")
            for event in store.structure_events(args.run_id):
                print(canonical_json(event))
        else:
            metadata = store.describe(args.dataset_id)
            calendar = SessionCalendar.from_dict(metadata["provenance"]["calendar"])
            if args.command == "aggregate":
                contract = MgcContract(str(metadata["provenance"]["contract"]))
                timeframes = tuple(args.timeframe or Timeframe)
                aggregator = CandleAggregator(calendar, contract, timeframes)
                for bar in aggregator.consume(store.replay(args.dataset_id)):
                    print(
                        canonical_json(
                            {
                                "event": "aggregate_closed",
                                "dataset_id": args.dataset_id,
                                "event_id": (
                                    f"{args.dataset_id}/aggregate-v{AGGREGATION_VERSION}"
                                    f"/{bar.timeframe}/{bar.opened_at.isoformat()}"
                                ),
                                "data_kind": metadata["provenance"]["data_kind"],
                                **bar.as_dict(),
                            }
                        )
                    )
                return 0
            for candle in store.replay(args.dataset_id):
                event = {
                    "event": "candle_closed",
                    "dataset_id": args.dataset_id,
                    "event_id": f"{args.dataset_id}/{candle.opened_at.isoformat()}",
                    "data_kind": metadata["provenance"]["data_kind"],
                    **candle.as_dict(),
                }
                if calendar.sessions is not None:
                    session = calendar.session_at(candle.opened_at)
                    if session is None:
                        raise ValueError("stored candle has no matching trading session")
                    event["trade_date"] = session.trade_date.isoformat()
                print(canonical_json(event))
    except (OSError, ValueError, OverflowError, csv.Error, sqlite3.Error) as exc:
        print(f"aurum: {exc}", file=sys.stderr)
        return 1
    return 0


def _research_options(parser: ArgumentParser, *, dataset_argument: bool = True) -> None:
    if dataset_argument:
        parser.add_argument("dataset_id")
    parser.add_argument(
        "--timeframe", type=Timeframe, choices=Timeframe, default=Timeframe.FIVE_MINUTES
    )
    defaults = DEFAULT_STRUCTURE_CONFIG
    parser.add_argument(
        "--internal-pivot",
        type=int,
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        default=(defaults.internal.left, defaults.internal.right),
    )
    parser.add_argument(
        "--external-pivot",
        type=int,
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        default=(defaults.external.left, defaults.external.right),
    )
    parser.add_argument(
        "--break-mode", type=BreakMode, choices=BreakMode, default=defaults.breaks.mode
    )
    parser.add_argument("--atr-period", type=int, default=defaults.breaks.atr_period)
    parser.add_argument("--min-gap-ticks", type=int)
    parser.add_argument("--inversion-closes", type=int)
    parser.add_argument("--atr-buffer-bps", type=int, default=defaults.breaks.atr_buffer_bps)
    parser.add_argument(
        "--min-body-atr-bps", type=int, default=defaults.breaks.minimum_body_atr_bps
    )
    parser.add_argument(
        "--min-body-range-bps", type=int, default=defaults.breaks.minimum_body_range_bps
    )


def _structure_config(args: Namespace) -> StructureConfig:
    return StructureConfig(
        PivotRule(*args.internal_pivot),
        PivotRule(*args.external_pivot),
        BreakRule(
            args.break_mode,
            args.atr_period,
            args.atr_buffer_bps,
            args.min_body_atr_bps,
            args.min_body_range_bps,
        ),
    )


def _evidence_config(args: Namespace) -> EvidenceConfig:
    overrides = {
        name: value
        for name, value in (
            ("min_width_ticks", args.min_gap_ticks),
            ("inversion_closes", args.inversion_closes),
        )
        if value is not None
    }
    return replace(
        DEFAULT_EVIDENCE_CONFIG,
        imbalances=replace(DEFAULT_EVIDENCE_CONFIG.imbalances, **overrides),
    )


def _strategy_options(parser: ArgumentParser) -> None:
    _research_options(parser)
    parser.add_argument(
        "--variant", type=Variant, choices=Variant, default=DEFAULT_STRATEGY_CONFIG.variant
    )
    parser.add_argument("--session", type=ResearchSession, choices=ResearchSession, action="append")
    parser.add_argument("--regime", type=Regime, choices=Regime, action="append")
    parser.add_argument("--min-volatility-bps", type=int)
    parser.add_argument("--max-volatility-bps", type=int)
    parser.add_argument("--minimum-score", type=int, default=DEFAULT_STRATEGY_CONFIG.minimum_score)
    parser.add_argument(
        "--gross-risk-cents", type=int, default=DEFAULT_RISK_CONFIG.gross_allowance_cents
    )
    parser.add_argument("--net-risk-cents", type=int)
    parser.add_argument(
        "--target-mode",
        type=TargetMode,
        choices=TargetMode,
        default=DEFAULT_RISK_CONFIG.target_mode,
    )
    parser.add_argument("--economic-calendar", type=Path)
    parser.add_argument("--require-clear-event-risk", action="store_true")
    parser.add_argument(
        "--commission-cents", type=int, default=DEFAULT_RISK_CONFIG.commission_round_trip_cents
    )
    parser.add_argument(
        "--slippage-ticks", type=int, default=DEFAULT_RISK_CONFIG.slippage_round_trip_ticks
    )
    parser.add_argument("--max-contracts", type=int)


def _strategy_run(
    store: DatasetStore, args: Namespace, execution: ExecutionConfig | None = None
) -> str:
    risk = replace(
        DEFAULT_RISK_CONFIG,
        gross_allowance_cents=args.gross_risk_cents,
        net_allowance_cents=args.net_risk_cents,
        target_mode=args.target_mode,
        commission_round_trip_cents=args.commission_cents,
        slippage_round_trip_ticks=args.slippage_ticks,
        max_contracts=args.max_contracts,
    )
    config = replace(
        DEFAULT_STRATEGY_CONFIG,
        variant=args.variant,
        allowed_sessions=tuple(args.session or ()),
        allowed_regimes=tuple(args.regime or ()),
        minimum_score=args.minimum_score,
        risk=risk,
        minimum_volatility_bps=args.min_volatility_bps,
        maximum_volatility_bps=args.max_volatility_bps,
        require_clear_event_risk=args.require_clear_event_risk,
    )
    economic = (
        EconomicCalendar.from_json(args.economic_calendar.read_text(encoding="utf-8"))
        if args.economic_calendar
        else None
    )
    return store.build_strategy_run(
        args.dataset_id,
        args.timeframe,
        _structure_config(args),
        config,
        evidence=_evidence_config(args),
        economic=economic,
        execution=execution,
    )


def _monte_carlo(store: DatasetStore, args: Namespace) -> str:
    config = MonteCarloConfig(
        paths=args.paths,
        trades=args.trades,
        seed=args.seed,
        block_length=args.block_length,
        initial_equity_cents=args.initial_equity_cents,
        loss_allowance_cents=args.loss_allowance_cents,
    )
    payouts = {
        name: value
        for name, value in (
            ("gross_win_cents", args.gross_win_cents),
            ("gross_loss_cents", args.gross_loss_cents),
            ("minimum_cost_cents", args.minimum_cost_cents),
            ("maximum_cost_cents", args.maximum_cost_cents),
        )
        if value is not None
    }
    if args.mode is SamplingMode.SCENARIO:
        rates = args.win_rate_bps or REFERENCE_WIN_RATES_BPS
        scenarios = tuple(WinRateScenario(rate, **payouts) for rate in rates)
    elif payouts or args.win_rate_bps:
        raise ValueError("assumed payouts and win rates require scenario mode")
    else:
        scenarios = ()
    trailing = (
        TrailingLossScenario(LUCIDFLEX_25K, args.lucidflex_trades_per_session)
        if args.lucidflex_trades_per_session is not None
        else None
    )
    return simulate_report(store, config, args.mode, scenarios, args.backtest, trailing=trailing)


def _import_options(parser: ArgumentParser) -> None:
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--source-timezone", required=True)
    parser.add_argument(
        "--timestamp-label", type=TimestampLabel, choices=TimestampLabel, required=True
    )
    parser.add_argument("--data-kind", type=DataKind, choices=DataKind, required=True)
    parser.add_argument("--price-basis", choices=("unadjusted",), required=True)


def _import_history(store: DatasetStore, args: Namespace) -> str:
    calendar = SessionCalendar.from_json(args.calendar.read_text(encoding="utf-8"))
    config = ImportConfig(
        args.provider,
        MgcContract(args.contract),
        args.source_timezone,
        args.timestamp_label,
        args.data_kind,
    )
    source: FileHistorySource | HttpsHistorySource
    if args.command == "import-csv":
        source = FileHistorySource(args.csv, SourceFormat.CSV)
    elif args.command == "import-parquet":
        source = FileHistorySource(args.parquet, SourceFormat.PARQUET)
    else:
        source = HttpsHistorySource(args.url, args.format, args.token_env)
    return store.save(prepare_history(source, config, calendar))


def _monitor_live(store: DatasetStore, args: Namespace) -> dict[str, object]:
    calendar = SessionCalendar.from_json(args.calendar.read_text(encoding="utf-8"))
    source = WebSocketSource(args.endpoint, args.feed_status, args.token_env)
    config = MonitorConfig(
        args.source,
        args.feed_status,
        stale_seconds=args.stale_seconds,
        timeframe=args.timeframe,
        source_origin=source.origin,
        source_fingerprint=source.fingerprint,
    )
    instance = MarketMonitor(
        store,
        calendar,
        args.contract,
        config,
        _structure_config(args),
        replace(DEFAULT_STRATEGY_CONFIG, variant=args.variant),
        _evidence_config(args),
    )
    backfill = (
        HttpsGapBackfill(args.backfill_url, args.backfill_token_env) if args.backfill_url else None
    )
    print(f"aurum: paper monitor {instance.monitor_id}", file=sys.stderr)
    previous = signal.signal(signal.SIGTERM, _stop_monitor)
    try:
        return run_stream(instance, source, backfill=backfill, max_frames=args.max_frames)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _stop_monitor(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _monitor_replay(store: DatasetStore, args: Namespace) -> dict[str, object]:
    dataset = store.describe(args.dataset_id)
    if not dataset["replayable"]:
        raise ValueError("replay blocked because expected candles are missing")
    provenance = dataset["provenance"]
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    mode = FeedStatus.SIMULATED if provenance["data_kind"] == "synthetic" else FeedStatus.HISTORICAL
    contract = MgcContract(str(provenance["contract"]))
    config = MonitorConfig(f"replay:{args.dataset_id}", mode, timeframe=args.timeframe)
    instance = MarketMonitor(
        store,
        calendar,
        contract,
        config,
        _structure_config(args),
        replace(DEFAULT_STRATEGY_CONFIG, variant=args.variant),
        _evidence_config(args),
    )
    existing = describe_monitor(store, instance.monitor_id)
    expected = existing["next_expected_open"]
    if expected is None:
        return existing
    start = datetime.fromisoformat(str(expected))
    end = datetime.fromisoformat(dataset["last_close"])
    instance.set_connection(True, start)
    for batch in store.replay_batches(args.dataset_id, TradingWindow(start, end)):
        for candle in batch:
            instance.ingest(FeedFrame.from_replay(candle, mode, candle.available_at))
    return instance.set_connection(False, end)
