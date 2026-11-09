import csv
import sqlite3
import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path

from aurum.candles import AGGREGATION_VERSION, CandleAggregator, Timeframe
from aurum.exchange_calendar import SessionCalendar
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, canonical_json, prepare_csv
from aurum.market_structure import (
    DEFAULT_STRUCTURE_CONFIG,
    BreakMode,
    BreakRule,
    PivotRule,
    StructureConfig,
)
from aurum.storage import DatasetStore


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
    structure.add_argument("dataset_id")
    structure.add_argument(
        "--timeframe", type=Timeframe, choices=Timeframe, default=Timeframe.FIVE_MINUTES
    )
    defaults = DEFAULT_STRUCTURE_CONFIG
    structure.add_argument(
        "--internal-pivot",
        type=int,
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        default=(defaults.internal.left, defaults.internal.right),
    )
    structure.add_argument(
        "--external-pivot",
        type=int,
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        default=(defaults.external.left, defaults.external.right),
    )
    structure.add_argument(
        "--break-mode", type=BreakMode, choices=BreakMode, default=defaults.breaks.mode
    )
    structure.add_argument("--atr-period", type=int, default=defaults.breaks.atr_period)
    structure.add_argument("--atr-buffer-bps", type=int, default=defaults.breaks.atr_buffer_bps)
    structure.add_argument(
        "--min-body-atr-bps", type=int, default=defaults.breaks.minimum_body_atr_bps
    )
    structure.add_argument(
        "--min-body-range-bps", type=int, default=defaults.breaks.minimum_body_range_bps
    )
    structure_events = commands.add_parser(
        "structure-events", help="read persisted events as JSONL"
    )
    structure_events.add_argument("run_id")

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
            structure_config = StructureConfig(
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
            run = store.build_structure_run(args.dataset_id, args.timeframe, structure_config)
            print(canonical_json(store.describe_structure_run(run)))
        elif args.command == "structure-events":
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
