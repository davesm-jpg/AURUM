import csv
import sqlite3
import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path

from aurum.exchange_calendar import SessionCalendar
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, canonical_json, prepare_csv
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
        else:
            metadata = store.describe(args.dataset_id)
            calendar = SessionCalendar.from_dict(metadata["provenance"]["calendar"])
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
