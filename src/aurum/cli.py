import csv
import os
import signal
import sqlite3
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path
from time import sleep

from aurum.account_reports import simulate_account_ledger
from aurum.account_simulation import LUCIDFLEX_25K, AccountPhase, BreachMetric
from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.continuous import SeriesPlan, replay_series, save_series
from aurum.contract_reports import save_contract_assessment
from aurum.contracts import ContractCatalog, RollPolicy
from aurum.control import ControlConfig, ControlStrata, save_matched_control
from aurum.databento_live import DatabentoSource, bind_native_resume
from aurum.dataset_audit import verify_dataset
from aurum.economic_archive import (
    EconomicPublisher,
    capture_official_calendar,
    economic_capture_bytes,
    economic_receipt,
    economic_receipts,
    normalize_archived_ics,
)
from aurum.economic_composition import compose_economic_calendars
from aurum.economic_events import MAX_ECONOMIC_PUBLICATIONS, EconomicCalendar, EconomicSourceKind
from aurum.economic_ics import compare_economic_ics, normalize_economic_ics
from aurum.economic_polling import (
    EconomicPollConfig,
    describe_economic_poller,
    economic_poll_attempts,
    economic_pollers,
    poll_economic_calendar,
    register_economic_poller,
)
from aurum.economic_updates import adopt_economic_snapshot, economic_updates
from aurum.email_transport import SmtpAlertChannel, SmtpSettings
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
from aurum.holdout import save_holdout_evaluation
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
from aurum.monitor_audit import verify_monitor
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
from aurum.quotes import (
    DEFAULT_QUOTE_MAX_AGE_MS,
    QuoteImportConfig,
    describe_quote_series,
    import_quote_csv,
    list_quote_series,
    quote_at,
    verify_quote_series,
)
from aurum.reconciliation import save_reconciliation
from aurum.report_notifications import queue_forward_report
from aurum.research_audit import verify_research_run
from aurum.research_reports import simulate_report, validate_runs
from aurum.runtime_profiles import list_runtime_profiles, load_delivery_profile, provider_runtime
from aurum.sensitivity_reports import SensitivityFactor, save_parameter_sensitivity
from aurum.spread_reports import save_spread_report
from aurum.statistics import ValidationConfig
from aurum.storage import DatasetStore, is_transient_lock
from aurum.storage_admin import backup_store, verify_store
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, Variant
from aurum.streaming import FrameSource, HttpsGapBackfill, WebSocketSource, run_stream
from aurum.telegram_setup import inspect_telegram
from aurum.timestamps import event_timestamp, minute_timestamp, trade_timestamp


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
    _import_options(trade_importer, candle_labels=False)
    quote_importer = commands.add_parser(
        "import-quotes", help="store normalized historical best bid and ask observations"
    )
    quote_importer.add_argument("csv", type=Path)
    _import_options(quote_importer, candle_labels=False)
    parquet = commands.add_parser("import-parquet", help="validate and store Parquet MGC candles")
    parquet.add_argument("parquet", type=Path)
    _import_options(parquet)
    databento = commands.add_parser(
        "import-databento-csv", help="import pretty-formatted Databento ohlcv-1m exports"
    )
    databento.add_argument("csv", type=Path)
    _import_options(databento, candle_labels=False)
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
    dataset_audit = commands.add_parser(
        "verify-dataset", help="verify normalized dataset rows against preserved source bytes"
    )
    dataset_audit.add_argument("dataset_id")
    prints = commands.add_parser("trade-prints", help="read preserved normalized trade prints")
    prints.add_argument("dataset_id")
    prints.add_argument("--start", type=trade_timestamp)
    prints.add_argument("--end", type=trade_timestamp)
    prints.add_argument("--after-timestamp", type=trade_timestamp)
    prints.add_argument("--after-sequence", type=int)
    prints.add_argument("--limit", type=int)
    quote_series = commands.add_parser("quote-series", help="inspect saved quote provenance")
    quote_series.add_argument("series_id", nargs="?")
    quote_audit = commands.add_parser(
        "verify-quote-series", help="verify quote observations against preserved source bytes"
    )
    quote_audit.add_argument("series_id")
    quote_lookup = commands.add_parser("quote-at", help="read the prior quote at a UTC instant")
    quote_lookup.add_argument("series_id")
    quote_lookup.add_argument("--at", type=event_timestamp, required=True)
    quote_lookup.add_argument("--maximum-age-ms", type=int, default=DEFAULT_QUOTE_MAX_AGE_MS)
    economic_ics = commands.add_parser(
        "normalize-economic-ics", help="convert a local iCalendar capture to a partial snapshot"
    )
    economic_ics.add_argument("ics", type=Path)
    economic_ics.add_argument("--observed-at", type=event_timestamp)
    economic_comparison = commands.add_parser(
        "compare-economic-ics", help="compare ordered local iCalendar captures of one source"
    )
    economic_comparison.add_argument(
        "--capture", action="append", nargs=2, metavar=("OBSERVED_AT", "FILE"), required=True
    )
    for economic_command in (economic_ics, economic_comparison):
        economic_command.add_argument("--source", required=True)
        economic_command.add_argument("--start", type=minute_timestamp, required=True)
        economic_command.add_argument("--end", type=minute_timestamp, required=True)
        economic_command.add_argument("--title-prefix", action="append", required=True)
        economic_command.add_argument(
            "--data-kind", type=EconomicSourceKind, choices=EconomicSourceKind, required=True
        )
    economic_bundle = commands.add_parser(
        "compose-economic-calendars", help="combine separately reviewed local calendar snapshots"
    )
    economic_bundle.add_argument("snapshots", type=Path, nargs="+")
    economic_bundle.add_argument("--source", required=True)
    fetch_calendar = commands.add_parser(
        "capture-economic-calendar",
        aliases=("capture-economic-ics",),
        help="archive an official economic calendar publication",
    )
    fetch_calendar.add_argument("publisher", type=EconomicPublisher, choices=EconomicPublisher)
    calendar_poll = commands.add_parser(
        "poll-economic-calendar", help="capture due public calendar receipts for later review"
    )
    calendar_poll.add_argument("publisher", type=EconomicPublisher, choices=EconomicPublisher)
    calendar_poll.add_argument("--interval-seconds", type=int, default=3600)
    calendar_poll.add_argument("--retry-seconds", type=int, default=300)
    calendar_poll.add_argument("--max-retry-seconds", type=int, default=21600)
    calendar_poll.add_argument("--watch", action="store_true")
    calendar_poll.add_argument("--max-attempts", type=int)
    pollers = commands.add_parser("economic-pollers", help="read public calendar polling health")
    pollers.add_argument("--limit", type=int, default=100)
    poll_audit = commands.add_parser("economic-poll-attempts", help="read durable polling attempts")
    poll_audit.add_argument("poller_id")
    poll_audit.add_argument("--after", type=int, default=0)
    poll_audit.add_argument("--limit", type=int, default=100)
    receipt = commands.add_parser("economic-captures", help="read archived capture receipts")
    receipt.add_argument("capture_id", nargs="?")
    receipt.add_argument("--limit", type=int, default=100)
    raw_calendar = commands.add_parser(
        "economic-capture-raw", help="export preserved publication bytes"
    )
    raw_calendar.add_argument("capture_id")
    archived_calendar = commands.add_parser(
        "normalize-economic-captures", help="normalize stored captures using their receipt times"
    )
    archived_calendar.add_argument("capture_ids", nargs="+")
    archived_calendar.add_argument("--start", type=minute_timestamp, required=True)
    archived_calendar.add_argument("--end", type=minute_timestamp, required=True)
    archived_calendar.add_argument("--title-prefix", action="append", required=True)
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
    spread = commands.add_parser(
        "spread-observations", help="audit quote coverage at saved paper references"
    )
    spread.add_argument("run_id")
    spread.add_argument("quote_series_id")
    spread.add_argument("--maximum-age-ms", type=int, default=DEFAULT_QUOTE_MAX_AGE_MS)
    spread.add_argument("--example-limit", type=int, default=10)
    spread.add_argument("--quote-cost-proxy", action="store_true")
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
    validation.add_argument("--selection-only", action="store_true")
    holdout = commands.add_parser(
        "evaluate-holdout", help="replay a saved selection's final paper period from flat"
    )
    holdout.add_argument("selection_id")
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
    monitor.add_argument("--lifecycle-alerts", action="store_true")
    _strategy_context_options(monitor)
    native_monitor = commands.add_parser(
        "monitor-databento", help="explicitly subscribe to an authorized native MGC minute feed"
    )
    _research_options(native_monitor, dataset_argument=False)
    native_monitor.add_argument("--calendar", type=Path, required=True)
    native_monitor.add_argument("--contract", type=MgcContract, required=True)
    native_monitor.add_argument("--raw-symbol", required=True)
    native_monitor.add_argument("--key-env", default="DATABENTO_API_KEY")
    native_monitor.add_argument("--start", type=minute_timestamp)
    native_monitor.add_argument("--stale-seconds", type=int, default=90)
    native_monitor.add_argument("--max-frames", type=int)
    native_monitor.add_argument("--lifecycle-alerts", action="store_true")
    _strategy_context_options(native_monitor)
    monitor_replay = commands.add_parser(
        "monitor-replay", help="exercise the monitor with a saved dataset"
    )
    _research_options(monitor_replay)
    _strategy_context_options(monitor_replay)
    for name in ("monitor-status", "monitor-events", "monitor-alerts"):
        command = commands.add_parser(name)
        command.add_argument("monitor_id")
    monitor_verify = commands.add_parser(
        "verify-monitor", help="audit preserved monitor input frames offline"
    )
    monitor_verify.add_argument("monitor_id")
    economic_update = commands.add_parser(
        "monitor-economic-update", help="activate a reviewed economic snapshot for a monitor"
    )
    economic_update.add_argument("monitor_id")
    update_source = economic_update.add_mutually_exclusive_group(required=True)
    update_source.add_argument("--snapshot", type=Path)
    update_source.add_argument("--unavailable", action="store_true")
    economic_update.add_argument("--reason", required=True)
    update_audit = commands.add_parser(
        "monitor-economic-updates", help="read adopted economic updates"
    )
    update_audit.add_argument("monitor_id")

    forward_start = commands.add_parser("forward-start", help="register a forward paper run")
    forward_start.add_argument("monitor_id")
    forward_start.add_argument("--synthetic", action="store_true")
    _execution_options(forward_start)
    forward_sync = commands.add_parser("forward-sync", help="consume the applied monitor journal")
    forward_sync.add_argument("forward_id")
    forward_sync.add_argument("--watch", action="store_true")
    forward_sync.add_argument("--poll-seconds", type=float, default=2.0)
    forward_sync.add_argument("--auto-reports", action="store_true")
    forward_sync.add_argument("--notify-reports", action="store_true")
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
    notify_report = commands.add_parser("notify-report", help="queue a saved forward report once")
    notify_report.add_argument("report_id")

    telegram_check = commands.add_parser(
        "telegram-inspect", help="inspect bot/private chat metadata without sending or changing it"
    )
    telegram_check.add_argument("--token-env", default="AURUM_TELEGRAM_TOKEN")
    run_audit = commands.add_parser(
        "verify-run", help="reconstruct a finalized full research journal from preserved input"
    )
    run_audit.add_argument("run_id")

    delivery = commands.add_parser("deliver", help="deliver paper alerts to an authorized channel")
    delivery.add_argument("monitor_id")
    delivery.add_argument("--channel", type=Channel, choices=Channel, required=True)
    delivery.add_argument("--destination-env", required=True)
    delivery.add_argument("--token-env")
    delivery.add_argument("--smtp-env")
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
    serve.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    serve.add_argument("--token-env")

    backup = commands.add_parser("backup", help="save a verified local SQLite snapshot")
    backup.add_argument("output", type=Path)
    backup.add_argument("--timeout-seconds", type=float, default=120)
    backup.add_argument("--verify-normalized", action="store_true")
    backup.add_argument("--verify-research", action="store_true")
    verify = commands.add_parser(
        "verify-store", help="verify database and preserved source integrity"
    )
    verify.add_argument("--full", action="store_true")
    verify.add_argument("--verify-sources", action="store_true")
    verify.add_argument("--verify-normalized", action="store_true")
    verify.add_argument("--verify-research", action="store_true")

    args = parser.parse_args(argv)
    store = DatasetStore(args.db)
    try:
        if args.command == "serve":
            _serve(store, args)
        elif args.command == "telegram-inspect":
            print(canonical_json(inspect_telegram(args.token_env)))
        elif args.command == "verify-run":
            print(canonical_json(verify_research_run(store, args.run_id)))
        elif args.command == "backup":
            print(
                canonical_json(
                    backup_store(
                        store,
                        args.output,
                        timeout_seconds=args.timeout_seconds,
                        verify_normalized=args.verify_normalized,
                        verify_research=args.verify_research,
                    )
                )
            )
        elif args.command == "verify-store":
            print(
                canonical_json(
                    verify_store(
                        store,
                        full=args.full,
                        verify_sources=args.verify_sources,
                        verify_normalized=args.verify_normalized,
                        verify_research=args.verify_research,
                    )
                )
            )
        elif args.command in (
            "import-csv",
            "import-parquet",
            "import-https",
            "import-trades",
            "import-databento-csv",
        ):
            print(canonical_json(store.describe(_import_history(store, args))))
        elif args.command == "import-quotes":
            calendar = SessionCalendar.from_json(args.calendar.read_text(encoding="utf-8"))
            quote_config = QuoteImportConfig(
                args.provider, MgcContract(args.contract), args.source_timezone, args.data_kind
            )
            series = import_quote_csv(store, args.csv, quote_config, calendar)
            print(canonical_json(describe_quote_series(store, series)))
        elif args.command == "quote-series":
            print(
                canonical_json(
                    describe_quote_series(store, args.series_id)
                    if args.series_id
                    else list_quote_series(store)
                )
            )
        elif args.command == "quote-at":
            print(canonical_json(quote_at(store, args.series_id, args.at, args.maximum_age_ms)))
        elif args.command == "verify-quote-series":
            print(canonical_json(verify_quote_series(store, args.series_id)))
        elif args.command == "normalize-economic-ics":
            observed = args.observed_at
            if observed is not None and observed.nanosecond_tail:
                raise ValueError("economic capture time supports microsecond precision")
            snapshot = normalize_economic_ics(
                args.ics,
                source=args.source,
                coverage=TradingWindow(args.start, args.end),
                observed_at=observed.time if observed is not None else datetime.now(UTC),
                data_kind=args.data_kind,
                title_prefixes=tuple(args.title_prefix),
            )
            print(canonical_json(snapshot.as_dict()))
        elif args.command == "compare-economic-ics":
            inputs = []
            for raw_observed, path in args.capture:
                observed = event_timestamp(raw_observed)
                if observed.nanosecond_tail:
                    raise ValueError("economic capture time supports microsecond precision")
                inputs.append((Path(path), observed.time))
            snapshot = compare_economic_ics(
                tuple(inputs),
                source=args.source,
                coverage=TradingWindow(args.start, args.end),
                data_kind=args.data_kind,
                title_prefixes=tuple(args.title_prefix),
            )
            print(canonical_json(snapshot.as_dict()))
        elif args.command == "compose-economic-calendars":
            if not 2 <= len(args.snapshots) <= MAX_ECONOMIC_PUBLICATIONS:
                raise ValueError(f"provide between 2 and {MAX_ECONOMIC_PUBLICATIONS} snapshots")
            snapshots = tuple(
                EconomicCalendar.from_json(path.read_text(encoding="utf-8"))
                for path in args.snapshots
            )
            print(
                canonical_json(compose_economic_calendars(snapshots, source=args.source).as_dict())
            )
        elif args.command in ("capture-economic-calendar", "capture-economic-ics"):
            print(canonical_json(capture_official_calendar(store, args.publisher).as_dict()))
        elif args.command == "poll-economic-calendar":
            print(canonical_json(_poll_calendar(store, args)))
        elif args.command == "economic-pollers":
            print(canonical_json(economic_pollers(store, args.limit)))
        elif args.command == "economic-poll-attempts":
            print(
                canonical_json(
                    economic_poll_attempts(
                        store, args.poller_id, after=args.after, limit=args.limit
                    )
                )
            )
        elif args.command == "economic-captures":
            capture_details = (
                economic_receipt(store, args.capture_id).as_dict()
                if args.capture_id is not None
                else [receipt.as_dict() for receipt in economic_receipts(store, args.limit)]
            )
            print(canonical_json(capture_details))
        elif args.command == "economic-capture-raw":
            sys.stdout.buffer.write(
                economic_capture_bytes(store, economic_receipt(store, args.capture_id))
            )
        elif args.command == "normalize-economic-captures":
            snapshot = normalize_archived_ics(
                store,
                tuple(args.capture_ids),
                coverage=TradingWindow(args.start, args.end),
                title_prefixes=tuple(args.title_prefix),
            )
            print(canonical_json(snapshot.as_dict()))
        elif args.command == "monitor-economic-update":
            update_id = adopt_economic_snapshot(
                store,
                args.monitor_id,
                _economic_calendar(args.snapshot),
                activated_at=datetime.now(UTC),
                reason=args.reason,
            )
            print(canonical_json({"update_id": update_id}))
        elif args.command == "monitor-economic-updates":
            print(canonical_json(economic_updates(store, args.monitor_id)))
        elif args.command == "contracts":
            inventory = ContractCatalog.from_json(args.catalog.read_text(encoding="utf-8"))
            policy = RollPolicy(
                args.expiry_lead_days, args.volume_lead_bps, args.open_interest_lead_bps
            )
            report_id = save_contract_assessment(store, inventory, args.as_of, args.current, policy)
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
                        profile.smtp_env,
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
        elif args.command == "notify-report":
            print(canonical_json({"alert_id": queue_forward_report(store, args.report_id)}))
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
        elif args.command == "monitor-databento":
            print(canonical_json(_monitor_databento(store, args)))
        elif args.command == "monitor-replay":
            print(canonical_json(_monitor_replay(store, args)))
        elif args.command == "monitor-status":
            status = describe_monitor(store, args.monitor_id)
            print(canonical_json(operational_monitor_status(status, datetime.now(UTC))))
        elif args.command == "verify-monitor":
            print(canonical_json(verify_monitor(store, args.monitor_id)))
        elif args.command in ("monitor-events", "monitor-alerts"):
            reader = monitor_events if args.command == "monitor-events" else monitor_alerts
            for event in reader(store, args.monitor_id):
                print(canonical_json(event))
        elif args.command == "inspect":
            print(canonical_json(store.describe(args.dataset_id)))
        elif args.command == "verify-dataset":
            print(canonical_json(verify_dataset(store, args.dataset_id)))
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
        elif args.command == "spread-observations":
            report_id = save_spread_report(
                store,
                args.run_id,
                args.quote_series_id,
                maximum_age_ms=args.maximum_age_ms,
                example_limit=args.example_limit,
                include_quote_cost_proxy=args.quote_cost_proxy,
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
            report_id = validate_runs(
                store, tuple(args.run_ids), validation_config, selection_only=args.selection_only
            )
            print(canonical_json(store.describe_analysis(report_id)))
        elif args.command == "evaluate-holdout":
            report_id = save_holdout_evaluation(store, args.selection_id)
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


def _poll_calendar(store: DatasetStore, args: Namespace) -> dict[str, object]:
    if args.max_attempts is not None and args.max_attempts < 1:
        raise ValueError("economic poll maximum attempts must be positive")
    if args.max_attempts is not None and not args.watch:
        raise ValueError("economic poll attempt limit requires --watch")
    config = EconomicPollConfig(
        args.publisher, args.interval_seconds, args.retry_seconds, args.max_retry_seconds
    )
    reference = register_economic_poller(store, config)
    print(f"aurum: public economic poller {reference}", file=sys.stderr)
    previous = signal.signal(signal.SIGTERM, _stop_monitor)
    attempts = 0
    try:
        while True:
            status = poll_economic_calendar(store, reference)
            if status["attempted"]:
                attempts += 1
                print(
                    f"aurum: {config.publisher.value.upper()} calendar capture {status['state']}",
                    file=sys.stderr,
                )
            if not args.watch or args.max_attempts is not None and attempts >= args.max_attempts:
                return status
            next_due = datetime.fromisoformat(str(status["next_due_at"]))
            latest = status["latest_attempt"]
            if isinstance(latest, dict) and latest["outcome"] == "fetching":
                next_due = datetime.fromisoformat(latest["lease_until"])
            sleep(max(0.25, min(30, (next_due - datetime.now(UTC)).total_seconds())))
    except KeyboardInterrupt:
        return describe_economic_poller(store, reference)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _sync_forward(store: DatasetStore, args: Namespace) -> dict[str, object]:
    if not 0.25 <= args.poll_seconds <= 60:
        raise ValueError("forward poll interval must be between 0.25 and 60 seconds")
    if args.report_baseline is not None and not args.auto_reports:
        raise ValueError("automatic report baseline requires --auto-reports")
    if args.notify_reports and not args.auto_reports:
        raise ValueError("report notifications require --auto-reports")
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
                        store,
                        args.forward_id,
                        baseline_validation_id=args.report_baseline,
                        notify=args.notify_reports,
                    )
                    reports_pending = False
                    if published:
                        last_publications = published
                        for reference in published:
                            print(f"aurum: saved forward report {reference}", file=sys.stderr)
            except sqlite3.OperationalError as exc:
                if not args.watch or not is_transient_lock(exc):
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


def _import_options(parser: ArgumentParser, *, candle_labels: bool = True) -> None:
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--source-timezone", required=True)
    if candle_labels:
        parser.add_argument(
            "--timestamp-label", type=TimestampLabel, choices=TimestampLabel, required=True
        )
    else:
        parser.set_defaults(timestamp_label=TimestampLabel.OPEN)
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
    elif args.command == "import-databento-csv":
        source = FileHistorySource(args.csv, SourceFormat.DATABENTO_OHLCV_CSV)
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
        lifecycle_alerts=args.lifecycle_alerts,
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
    source: FrameSource,
    backfill: HttpsGapBackfill | None,
    max_frames: int | None,
) -> dict[str, object]:
    print(f"aurum: paper monitor {instance.monitor_id}", file=sys.stderr)
    previous = signal.signal(signal.SIGTERM, _stop_monitor)
    try:
        return run_stream(instance, source, backfill=backfill, max_frames=max_frames)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _monitor_databento(store: DatasetStore, args: Namespace) -> dict[str, object]:
    source = DatabentoSource(args.contract, args.raw_symbol, args.key_env, args.start)
    calendar = SessionCalendar.from_json(args.calendar.read_text(encoding="utf-8"))
    instance = MarketMonitor(
        store,
        calendar,
        args.contract,
        MonitorConfig(
            "Databento GLBX.MDP3 native ohlcv-1m",
            source.mode,
            stale_seconds=args.stale_seconds,
            timeframe=args.timeframe,
            source_origin=source.origin,
            source_fingerprint=source.fingerprint,
            lifecycle_alerts=args.lifecycle_alerts,
        ),
        _structure_config(args),
        replace(
            DEFAULT_STRATEGY_CONFIG,
            variant=args.variant,
            require_clear_event_risk=args.require_clear_event_risk,
        ),
        _evidence_config(args),
        economic=_economic_calendar(args.economic_calendar),
    )

    return _watch_monitor(instance, bind_native_resume(source, instance), None, args.max_frames)


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
    if args.host == "0.0.0.0" and not token:
        raise ValueError("binding the API to all interfaces requires a configured access token")
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
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


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
        args.smtp_env,
    )


def _run_delivery(
    store: DatasetStore,
    monitor_id: str,
    channel: Channel,
    destination_env: str,
    token_env: str | None,
    preferences: DeliveryPreferences,
    watch: bool,
    smtp_env: str | None = None,
) -> dict[str, object]:
    destination = os.environ.get(destination_env)
    if not destination:
        raise ValueError("configured notification destination variable is unset")
    transport: HttpAlertChannel | SmtpAlertChannel
    if channel is Channel.EMAIL:
        settings = os.environ.get(smtp_env) if smtp_env else None
        password = os.environ.get(token_env) if token_env else None
        if not settings or not password:
            raise ValueError("configured SMTP settings or password variable is unset")
        transport = SmtpAlertChannel(SmtpSettings.from_json(settings), destination, password)
    elif channel is Channel.TELEGRAM:
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
