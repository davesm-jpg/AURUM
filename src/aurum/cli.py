import csv
import sqlite3
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.economic_events import EconomicCalendar
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG, EvidenceConfig
from aurum.exchange_calendar import SessionCalendar
from aurum.execution import DEFAULT_EXECUTION_CONFIG, ExecutionConfig
from aurum.instruments import MgcContract
from aurum.market_context import Regime, ResearchSession
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, canonical_json, prepare_csv
from aurum.market_structure import (
    DEFAULT_STRUCTURE_CONFIG,
    BreakMode,
    BreakRule,
    PivotRule,
    StructureConfig,
)
from aurum.paper_risk import DEFAULT_RISK_CONFIG, TargetMode
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, Variant


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(prog="aurum", description="MGC historical research data")
    parser.add_argument("--db", type=Path, required=True, help="SQLite dataset store")
    commands = parser.add_subparsers(dest="command", required=True)

    importer = commands.add_parser("import-csv", help="validate and store unadjusted MGC candles")
    importer.add_argument("csv", type=Path)
    importer.add_argument("--calendar", type=Path, required=True)
    importer.add_argument("--provider", required=True)
    importer.add_argument("--contract", required=True)
    importer.add_argument("--source-timezone", required=True)
    importer.add_argument(
        "--timestamp-label", type=TimestampLabel, choices=TimestampLabel, required=True
    )
    importer.add_argument("--data-kind", type=DataKind, choices=DataKind, required=True)
    importer.add_argument("--price-basis", choices=("unadjusted",), required=True)

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

    args = parser.parse_args(argv)
    store = DatasetStore(args.db)
    try:
        if args.command == "import-csv":
            calendar = SessionCalendar.from_json(args.calendar.read_text(encoding="utf-8"))
            config = ImportConfig(
                args.provider,
                MgcContract(args.contract),
                args.source_timezone,
                args.timestamp_label,
                args.data_kind,
            )
            dataset_id = store.save(prepare_csv(args.csv, config, calendar))
            print(canonical_json(store.describe(dataset_id)))
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


def _research_options(parser: ArgumentParser) -> None:
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
