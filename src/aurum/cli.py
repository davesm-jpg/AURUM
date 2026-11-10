import csv
import os
import signal
import sqlite3
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path
from time import sleep

from aurum.account_reports import simulate_account_ledger
from aurum.account_simulation import LUCIDFLEX_25K, AccountPhase, BreachMetric
from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.continuous import SeriesPlan, replay_series, save_series
from aurum.contracts import CONTRACT_VERSION, ContractCatalog, RollPolicy
from aurum.control import ControlConfig, ControlStrata, save_matched_control
from aurum.economic_events import EconomicCalendar
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EvidenceConfig
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.execution import DEFAULT_EXECUTION_CONFIG, ExecutionConfig
from aurum.feed_frames import FeedFrame
from aurum.forward import (
    ForwardPaper,
    describe_forward,
    forward_events,
    list_forward_runs,
    register_forward,
)
from aurum.forward_reports import ForwardPeriod, publish_completed_periods, save_forward_report
from aurum.instruments import MgcContract
from aurum.intrabar_reports import save_intrabar_comparison
from aurum.market_context import Regime, ResearchSession
from aurum.market_data import (
    DataKind,
    ImportConfig,
    TimestampLabel,
    canonical_json,
    prepare_history,
    prepare_trade_csv,
)
from aurum.market_structure import (
    DEFAULT_STRUCTURE_CONFIG,
    BreakMode,
    BreakRule,
    PivotRule,
    StructureConfig,
)
from aurum.monitoring import (
    MAX_REPLAY_BATCH,
    MarketMonitor,
    MonitorConfig,
    describe_monitor,
    monitor_alerts,
    monitor_events,
    operational_monitor_status,
)
from aurum.monte_carlo import (
    REFERENCE_WIN_RATES_BPS,
    MonteCarloConfig,
    SamplingMode,
    TrailingLossScenario,
    WinRateScenario,
)
from aurum.notification_transport import HttpAlertChannel
from aurum.notifications import (
    Channel,
    DeliveryPreferences,
    DeliveryWorker,
    Severity,
    cancel_delivery,
    delivery_status,
)
from aurum.paper_risk import DEFAULT_RISK_CONFIG, TargetMode
from aurum.providers import FeedStatus, FileHistorySource, HttpsHistorySource, SourceFormat
from aurum.reconciliation import save_reconciliation
from aurum.research_reports import simulate_report, validate_runs
from aurum.runtime_profiles import list_runtime_profiles, load_delivery_profile, provider_runtime
from aurum.sensitivity_reports import SensitivityFactor, save_parameter_sensitivity
from aurum.statistics import ValidationConfig
from aurum.storage import DatasetStore
from aurum.storage_admin import backup_store, verify_store
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, Variant
from aurum.streaming import HttpsGapBackfill, WebSocketSource, run_stream
from aurum.timestamps import minute_timestamp, trade_timestamp


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(prog="aurum", description="MGC historical research data")
    parser.add_argument("--db", type=Path, required=True, help="SQLite dataset store")
    commands = parser.add_subparsers(dest="command", required=True)

    importer = commands.add_parser("import-csv", help="validate and store unadjusted MGC candles")
    importer.add_argument("csv", type=Path)
    _import_options(importer)
    trade_importer = commands.add_parser(
        "import-trades", help="store dated MGC trade prints and derive unadjusted minute bars"
    )
    trade_importer.add_argument("csv", type=Path)
    _import_options(trade_importer, trade_prints=True)
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
    prints = commands.add_parser("trade-prints", help="read preserved normalized trade prints")
    prints.add_argument("dataset_id")
    prints.add_argument("--start", type=trade_timestamp)
    prints.add_argument("--end", type=trade_timestamp)
    prints.add_argument("--after-timestamp", type=trade_timestamp)
    prints.add_argument("--after-sequence", type=int)
    prints.add_argument("--limit", type=int)
    reconcile = commands.add_parser(
        "reconcile-datasets", help="audit two dated imports over shared schedule coverage"
    )
    reconcile.add_argument("primary_dataset_id")
    reconcile.add_argument("comparison_dataset_id")
    reconcile.add_argument("--start", type=minute_timestamp)
    reconcile.add_argument("--end", type=minute_timestamp)
    reconcile.add_argument("--example-limit", type=int, default=20)
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
    _execution_options(backtest)
    backtest.add_argument("--resolve-with-trade-prints", action="store_true")
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
    intrabar = commands.add_parser(
        "compare-intrabar", help="pair stop-first and trade-print backtest outcomes"
    )
    intrabar.add_argument("stop_first_run_id")
    intrabar.add_argument("trade_print_run_id")
    intrabar.add_argument("--example-limit", type=int, default=20)
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
    sensitivity = commands.add_parser(
        "parameter-sensitivity", help="compare one factor in pre-holdout validation"
    )
    sensitivity.add_argument("validation_id")
    sensitivity.add_argument("baseline_run_id")
    sensitivity.add_argument(
        "--factor", type=SensitivityFactor, choices=SensitivityFactor, required=True
    )
    sensitivity.add_argument("--candidate", action="append")
    control = commands.add_parser(
        "matched-control", help="compare a holdout to development-fitted random admissions"
    )
    control.add_argument("validation_id")
    control.add_argument("opportunity_run_id")
    control.add_argument("--seed", type=int, default=42)
    control.add_argument("--replications", type=int, default=30)
    control.add_argument(
        "--strata",
        type=ControlStrata,
        choices=ControlStrata,
        default=ControlStrata.SESSION_DIRECTION,
    )
    control.add_argument("--bootstrap-draws", type=int, default=1000)
    control.add_argument("--block-length", type=int, default=5)
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
    _strategy_context_options(monitor)
    monitor_replay = commands.add_parser(
        "monitor-replay", help="exercise the monitor with a saved dataset"
    )
    _research_options(monitor_replay)
    _strategy_context_options(monitor_replay)
    for name in ("monitor-status", "monitor-events", "monitor-alerts"):
        command = commands.add_parser(name)
        command.add_argument("monitor_id")

    forward_start = commands.add_parser("forward-start", help="register a forward paper run")
    forward_start.add_argument("monitor_id")
    forward_start.add_argument("--synthetic", action="store_true")
    _execution_options(forward_start)
    forward_sync = commands.add_parser("forward-sync", help="consume the applied monitor journal")
    forward_sync.add_argument("forward_id")
    forward_sync.add_argument("--watch", action="store_true")
    forward_sync.add_argument("--poll-seconds", type=float, default=2.0)
    forward_sync.add_argument("--auto-reports", action="store_true")
    forward_sync.add_argument("--report-baseline")
    commands.add_parser("forward-runs", help="list forward paper runs")
    for name in ("forward-status", "forward-events"):
        command = commands.add_parser(name)
        command.add_argument("forward_id")
    forward_report = commands.add_parser("forward-report", help="save a forward research period")
    forward_report.add_argument("forward_id")
    forward_report.add_argument(
        "--period", type=ForwardPeriod, choices=ForwardPeriod, default=ForwardPeriod.DAILY
    )
    forward_report.add_argument("--trade-date", required=True, type=date.fromisoformat)
    forward_report.add_argument("--baseline-validation")
    forward_report.add_argument("--bootstrap-draws", type=int, default=1000)
    forward_report.add_argument("--block-length", type=int, default=5)
    forward_report.add_argument("--seed", type=int, default=42)

    delivery = commands.add_parser("deliver", help="deliver paper alerts to an authorized channel")
    delivery.add_argument("monitor_id")
    delivery.add_argument("--channel", type=Channel, choices=Channel, required=True)
    delivery.add_argument("--destination-env", required=True)
    delivery.add_argument("--token-env")
    delivery.add_argument("--timezone", default="Europe/Prague")
    delivery.add_argument("--quiet", nargs=2, type=_clock_minute, metavar=("START", "END"))
    delivery.add_argument("--min-score", type=int, default=0)
    delivery.add_argument(
        "--session", type=ResearchSession, choices=ResearchSession, action="append"
    )
    delivery.add_argument("--severity", type=Severity, choices=Severity, action="append")
    delivery.add_argument("--attempts-per-hour", type=int, default=20)
    delivery.add_argument("--max-attempts", type=int, default=4)
    delivery.add_argument("--expiry-minutes", type=int, default=15)
    delivery.add_argument("--watch", action="store_true")
    delivery_audit = commands.add_parser("delivery-status", help="read notification attempt audit")
    delivery_audit.add_argument("monitor_id")
    cancel_alert = commands.add_parser(
        "cancel-delivery", help="cancel queued delivery for an alert"
    )
    cancel_alert.add_argument("monitor_id")
    cancel_alert.add_argument("alert_id")

    commands.add_parser("runtime-profiles", help="list saved provider and delivery settings")
    monitor_profile = commands.add_parser("monitor-profile", help="run a saved market-data profile")
    monitor_profile.add_argument("profile_id")
    monitor_profile.add_argument("--max-frames", type=int)
    delivery_profile = commands.add_parser(
        "deliver-profile", help="run a saved paper delivery profile"
    )
    delivery_profile.add_argument("profile_id")
    delivery_profile.add_argument("--watch", action="store_true")

    serve = commands.add_parser("serve", help="serve the local AURUM research API")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--token-env")

    backup = commands.add_parser("backup", help="save a verified local SQLite snapshot")
    backup.add_argument("output", type=Path)
    backup.add_argument("--timeout-seconds", type=float, default=120)
    verify = commands.add_parser(
        "verify-store", help="verify database and preserved source integrity"
    )
    verify.add_argument("--full", action="store_true")
    verify.add_argument("--verify-sources", action="store_true")

    args = parser.parse_args(argv)
    store = DatasetStore(args.db)
    try:
        if args.command == "serve":
            _serve(store, args)
        elif args.command == "backup":
            print(
                canonical_json(
                    backup_store(store, args.output, timeout_seconds=args.timeout_seconds)
                )
            )
        elif args.command == "verify-store":
            print(
                canonical_json(
                    verify_store(store, full=args.full, verify_sources=args.verify_sources)
                )
            )
        elif args.command in ("import-csv", "import-parquet", "import-https", "import-trades"):
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
        elif args.command == "runtime-profiles":
            print(canonical_json(list_runtime_profiles(store)))
        elif args.command == "monitor-profile":
            instance, source, backfill = provider_runtime(store, args.profile_id)
            print(canonical_json(_watch_monitor(instance, source, backfill, args.max_frames)))
        elif args.command == "deliver-profile":
            profile = load_delivery_profile(store, args.profile_id)
            print(
                canonical_json(
                    _run_delivery(
                        store,
                        profile.monitor_id,
                        profile.channel,
                        profile.destination_env,
                        profile.token_env,
                        profile.preferences,
                        args.watch,
                    )
                )
            )
        elif args.command == "deliver":
            print(canonical_json(_deliver(store, args)))
        elif args.command == "delivery-status":
            for delivery_item in delivery_status(store, args.monitor_id):
                print(canonical_json(delivery_item))
        elif args.command == "cancel-delivery":
            cancel_delivery(store, args.monitor_id, args.alert_id, datetime.now(UTC))
            print(canonical_json({"alert_id": args.alert_id, "queued_delivery_canceled": True}))
        elif args.command == "forward-start":
            reference = register_forward(
                store,
                args.monitor_id,
                datetime.now(UTC),
                _execution_config(args),
                synthetic=args.synthetic,
            )
            print(canonical_json(describe_forward(store, reference)))
        elif args.command == "forward-sync":
            print(canonical_json(_sync_forward(store, args)))
        elif args.command == "forward-runs":
            print(canonical_json(list_forward_runs(store)))
        elif args.command == "forward-report":
            reference = save_forward_report(
                store,
                args.forward_id,
                args.period,
                args.trade_date,
                baseline_validation_id=args.baseline_validation,
                bootstrap_draws=args.bootstrap_draws,
                block_length=args.block_length,
                seed=args.seed,
            )
            print(canonical_json(store.describe_analysis(reference)))
        elif args.command == "forward-status":
            print(canonical_json(describe_forward(store, args.forward_id)))
        elif args.command == "forward-events":
            after = 0
            remaining = int(str(describe_forward(store, args.forward_id)["event_count"]))
            while remaining:
                events = forward_events(
                    store, args.forward_id, after=after, limit=min(remaining, 500)
                )
                if not events:
                    break
                for event in events:
                    print(canonical_json(event))
                after = int(str(events[-1]["sequence"]))
                remaining -= len(events)
        elif args.command == "monitor":
            print(canonical_json(_monitor_live(store, args)))
        elif args.command == "monitor-replay":
            print(canonical_json(_monitor_replay(store, args)))
        elif args.command == "monitor-status":
            status = describe_monitor(store, args.monitor_id)
            print(canonical_json(operational_monitor_status(status, datetime.now(UTC))))
        elif args.command in ("monitor-events", "monitor-alerts"):
            reader = monitor_events if args.command == "monitor-events" else monitor_alerts
            for event in reader(store, args.monitor_id):
                print(canonical_json(event))
        elif args.command == "inspect":
            print(canonical_json(store.describe(args.dataset_id)))
        elif args.command == "trade-prints":
            if (args.after_timestamp is None) != (args.after_sequence is None):
                raise ValueError("trade cursor requires both timestamp and sequence")
            cursor = (
                (args.after_timestamp, args.after_sequence)
                if args.after_timestamp is not None
                else None
            )
            provenance = store.describe(args.dataset_id)["provenance"]
            for trade in store.trade_prints(
                args.dataset_id, start=args.start, end=args.end, after=cursor, limit=args.limit
            ):
                print(
                    canonical_json(
                        {
                            **trade.as_dict(),
                            "dataset_id": args.dataset_id,
                            "data_kind": provenance["data_kind"],
                            "event_id": f"{args.dataset_id}/{trade.sequence}",
                        }
                    )
                )
        elif args.command == "reconcile-datasets":
            if (args.start is None) != (args.end is None):
                raise ValueError("specify both comparison boundaries or neither")
            span = TradingWindow(args.start, args.end) if args.start is not None else None
            report_id = save_reconciliation(
                store,
                args.primary_dataset_id,
                args.comparison_dataset_id,
                span=span,
                example_limit=args.example_limit,
            )
            print(canonical_json(store.describe_analysis(report_id)))
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
            execution = _execution_config(args)
            print(canonical_json(store.describe_backtest(_strategy_run(store, args, execution))))
        elif args.command == "backtest-summary":
            print(canonical_json(store.describe_backtest(args.run_id)))
        elif args.command == "compare-intrabar":
            report_id = save_intrabar_comparison(
                store,
                args.stop_first_run_id,
                args.trade_print_run_id,
                example_limit=args.example_limit,
            )
            print(canonical_json(store.describe_analysis(report_id)))
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
        elif args.command == "matched-control":
            control_config = ControlConfig(
                args.seed, args.replications, args.strata, args.bootstrap_draws, args.block_length
            )
            report_id = save_matched_control(
                store, args.validation_id, args.opportunity_run_id, control_config
            )
            print(canonical_json(store.describe_analysis(report_id)))
        elif args.command == "parameter-sensitivity":
            report_id = save_parameter_sensitivity(
                store,
                args.validation_id,
                args.baseline_run_id,
                args.factor,
                run_ids=tuple(args.candidate or ()),
            )
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
    _strategy_context_options(parser)
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
    parser.add_argument(
        "--commission-cents", type=int, default=DEFAULT_RISK_CONFIG.commission_round_trip_cents
    )
    parser.add_argument(
        "--slippage-ticks", type=int, default=DEFAULT_RISK_CONFIG.slippage_round_trip_ticks
    )
    parser.add_argument("--max-contracts", type=int)


def _strategy_context_options(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--variant", type=Variant, choices=Variant, default=DEFAULT_STRATEGY_CONFIG.variant
    )
    parser.add_argument("--economic-calendar", type=Path)
    parser.add_argument("--require-clear-event-risk", action="store_true")


def _economic_calendar(path: Path | None) -> EconomicCalendar | None:
    return EconomicCalendar.from_json(path.read_text(encoding="utf-8")) if path else None


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
    return store.build_strategy_run(
        args.dataset_id,
        args.timeframe,
        _structure_config(args),
        config,
        evidence=_evidence_config(args),
        economic=_economic_calendar(args.economic_calendar),
        execution=execution,
        resolve_with_trade_prints=args.resolve_with_trade_prints
        if execution is not None
        else False,
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


def _execution_options(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--latency-minutes", type=int, default=DEFAULT_EXECUTION_CONFIG.latency_minutes
    )
    parser.add_argument(
        "--entry-timeout-minutes", type=int, default=DEFAULT_EXECUTION_CONFIG.entry_timeout_minutes
    )
    parser.add_argument(
        "--round-trip-spread-ticks",
        type=int,
        default=DEFAULT_EXECUTION_CONFIG.round_trip_spread_ticks,
    )
    parser.add_argument("--carry-across-closures", action="store_true")
    parser.add_argument(
        "--initial-equity-cents", type=int, default=DEFAULT_EXECUTION_CONFIG.initial_equity_cents
    )


def _execution_config(args: Namespace) -> ExecutionConfig:
    return ExecutionConfig(
        latency_minutes=args.latency_minutes,
        entry_timeout_minutes=args.entry_timeout_minutes,
        round_trip_spread_ticks=args.round_trip_spread_ticks,
        carry_across_closures=args.carry_across_closures,
        initial_equity_cents=args.initial_equity_cents,
    )


def _sync_forward(store: DatasetStore, args: Namespace) -> dict[str, object]:
    if not 0.25 <= args.poll_seconds <= 60:
        raise ValueError("forward poll interval must be between 0.25 and 60 seconds")
    if args.report_baseline is not None and not args.auto_reports:
        raise ValueError("automatic report baseline requires --auto-reports")
    runner = ForwardPaper(store, args.forward_id)
    reports_pending = args.auto_reports
    last_publications: list[str] = []
    retry = args.poll_seconds
    previous = signal.signal(signal.SIGTERM, _stop_monitor)
    try:
        while True:
            try:
                status = runner.sync()
                reports_pending |= args.auto_reports and bool(status["processed_minutes"])
                if status["caught_up"] and reports_pending:
                    published = publish_completed_periods(
                        store, args.forward_id, baseline_validation_id=args.report_baseline
                    )
                    reports_pending = False
                    if published:
                        last_publications = published
                        for reference in published:
                            print(f"aurum: saved forward report {reference}", file=sys.stderr)
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
                if not args.watch or code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                if retry == args.poll_seconds:
                    print(
                        "aurum: forward store busy, retrying the durable checkpoint",
                        file=sys.stderr,
                    )
                sleep(retry)
                retry = min(max(args.poll_seconds, 30.0), retry * 2)
                continue
            retry = args.poll_seconds
            if status["caught_up"]:
                if not args.watch:
                    return {**status, "published_report_ids": last_publications}
                sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return {
            **describe_forward(store, args.forward_id),
            "published_report_ids": last_publications,
        }
    finally:
        signal.signal(signal.SIGTERM, previous)


def _import_options(parser: ArgumentParser, *, trade_prints: bool = False) -> None:
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--source-timezone", required=True)
    if trade_prints:
        parser.set_defaults(timestamp_label=TimestampLabel.OPEN)
    else:
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
    if args.command == "import-trades":
        return store.save(prepare_trade_csv(args.csv, config, calendar))
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
        replace(
            DEFAULT_STRATEGY_CONFIG,
            variant=args.variant,
            require_clear_event_risk=args.require_clear_event_risk,
        ),
        _evidence_config(args),
        economic=_economic_calendar(args.economic_calendar),
    )
    backfill = (
        HttpsGapBackfill(args.backfill_url, args.backfill_token_env) if args.backfill_url else None
    )
    return _watch_monitor(instance, source, backfill, args.max_frames)


def _watch_monitor(
    instance: MarketMonitor,
    source: WebSocketSource,
    backfill: HttpsGapBackfill | None,
    max_frames: int | None,
) -> dict[str, object]:
    print(f"aurum: paper monitor {instance.monitor_id}", file=sys.stderr)
    previous = signal.signal(signal.SIGTERM, _stop_monitor)
    try:
        return run_stream(instance, source, backfill=backfill, max_frames=max_frames)
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
        replace(
            DEFAULT_STRATEGY_CONFIG,
            variant=args.variant,
            require_clear_event_risk=args.require_clear_event_risk,
        ),
        _evidence_config(args),
        economic=_economic_calendar(args.economic_calendar),
    )
    existing = describe_monitor(store, instance.monitor_id)
    expected = existing["next_expected_open"]
    if expected is None:
        return existing
    start = datetime.fromisoformat(str(expected))
    end = datetime.fromisoformat(dataset["last_close"])
    instance.set_connection(True, start)
    for batch in store.replay_batches(args.dataset_id, TradingWindow(start, end), MAX_REPLAY_BATCH):
        instance.ingest_replay_batch(
            tuple(FeedFrame.from_replay(candle, mode, candle.available_at) for candle in batch)
        )
    return instance.set_connection(False, end)


def _clock_minute(value: str) -> int:
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.hour * 60 + parsed.minute


def _serve(store: DatasetStore, args: Namespace) -> None:
    if not 1 <= args.port <= 65535:
        raise ValueError("API port must be between 1 and 65535")
    token = os.environ.get(args.token_env) if args.token_env else None
    if args.token_env and not token:
        raise ValueError("configured API token variable is unset")
    try:
        api = import_module("aurum.api")
        uvicorn = import_module("uvicorn")
        hosts = import_module("starlette.middleware.trustedhost")
    except ModuleNotFoundError as exc:
        raise ValueError("API support requires the aurum-research[api] extra") from exc
    with store.connection(write=True):
        pass
    app = api.create_app(store, token)
    app.add_middleware(
        hosts.TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


def _deliver(store: DatasetStore, args: Namespace) -> dict[str, object]:
    start, end = args.quiet or (None, None)
    prefs = DeliveryPreferences(
        timezone=args.timezone,
        quiet_start_minute=start,
        quiet_end_minute=end,
        min_score=args.min_score,
        allowed_sessions=tuple(args.session or ()),
        severities=tuple(args.severity or Severity),
        attempts_per_hour=args.attempts_per_hour,
        max_attempts=args.max_attempts,
        expiry_minutes=args.expiry_minutes,
    )
    return _run_delivery(
        store,
        args.monitor_id,
        args.channel,
        args.destination_env,
        args.token_env,
        prefs,
        args.watch,
    )


def _run_delivery(
    store: DatasetStore,
    monitor_id: str,
    channel: Channel,
    destination_env: str,
    token_env: str | None,
    preferences: DeliveryPreferences,
    watch: bool,
) -> dict[str, object]:
    destination = os.environ.get(destination_env)
    if not destination:
        raise ValueError("configured notification destination variable is unset")
    if channel is Channel.TELEGRAM:
        token = os.environ.get(token_env) if token_env else None
        if not token:
            raise ValueError("configured Telegram token variable is unset")
        transport = HttpAlertChannel.telegram(token, destination)
    else:
        transport = HttpAlertChannel.discord(destination)
    worker = DeliveryWorker(store, monitor_id, transport, preferences)
    attempts = 0
    try:
        while True:
            attempts += worker.run_once()
            if not watch:
                break
            sleep(5)
    except KeyboardInterrupt:
        pass
    return {"channel": channel, "monitor_id": monitor_id, "attempted": attempts}
