"""Measure AURUM's local pipeline using invented prices and session windows."""

import argparse
import csv
import platform
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TypeVar

from fastapi.testclient import TestClient

from aurum.api import create_app
from aurum.candles import Timeframe
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.execution import DEFAULT_EXECUTION_CONFIG
from aurum.feed_frames import FeedFrame
from aurum.instruments import MgcContract
from aurum.market_data import (
    COLUMNS,
    DataKind,
    ImportConfig,
    TimestampLabel,
    canonical_json,
    prepare_csv,
)
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import MarketMonitor, MonitorConfig
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG
from aurum.timestamps import MINUTE

_T = TypeVar("_T")
CONTRACT = MgcContract("MGC-2025-04")
FRAME = Timeframe.FIVE_MINUTES
START = datetime(2025, 1, 6, 13, tzinfo=UTC)


def _measure(operation: Callable[[], _T], repetitions: int = 1) -> tuple[float, _T]:
    samples = []
    for _ in range(repetitions):
        started = perf_counter()
        value = operation()
        samples.append(perf_counter() - started)
    return round(median(samples), 6), value


def _csv(path: Path, session_count: int, minutes: int) -> SessionCalendar:
    sessions = []
    day = START
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(COLUMNS)
        for day_index in range(session_count):
            while day.weekday() >= 5:
                day += timedelta(days=1)
            span = TradingWindow(day, day + minutes * MINUTE)
            sessions.append(TradingSession(day.date(), (span,)))
            for index in range(minutes):
                step = (index // 5 + day_index * 7) % 48
                swing = step if step < 24 else 48 - step
                base = 29_000 + 3 * swing + (index // 5 % 7) - day_index % 19
                offset = (index % 5) - 2
                opened = base + offset
                close = base + ((index * 7 + day_index) % 9) - 4
                prices = (opened, max(opened, close) + 3, min(opened, close) - 2, close)
                writer.writerow(
                    (
                        (day + index * MINUTE).isoformat(),
                        CONTRACT.code,
                        *(f"{price // 10}.{price % 10}" for price in prices),
                        1 + (index + day_index * 7) % 43,
                    )
                )
            day += timedelta(days=1)
    return SessionCalendar(
        "synthetic benchmark windows",
        "benchmark-v1",
        TradingWindow(sessions[0].start, sessions[-1].end),
        sessions=tuple(sessions),
    )


def _digest(value: object) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def run(directory: Path, session_count: int, minutes: int, repetitions: int) -> dict[str, object]:
    path = directory / "synthetic.csv"
    calendar = _csv(path, session_count, minutes)
    store = DatasetStore(directory / "synthetic.sqlite")
    config = ImportConfig(
        "synthetic benchmark", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC
    )
    elapsed = {}
    elapsed["import_and_store"], dataset_id = _measure(
        lambda: store.save(prepare_csv(path, config, calendar))
    )
    elapsed["evidence_replay"], evidence_id = _measure(
        lambda: store.build_structure_run(
            dataset_id, FRAME, DEFAULT_STRUCTURE_CONFIG, evidence=DEFAULT_EVIDENCE_CONFIG
        )
    )
    elapsed["paper_replay"], paper_id = _measure(
        lambda: store.build_strategy_run(
            dataset_id,
            FRAME,
            DEFAULT_STRUCTURE_CONFIG,
            DEFAULT_STRATEGY_CONFIG,
            execution=DEFAULT_EXECUTION_CONFIG,
        )
    )
    monitor_config = MonitorConfig("synthetic benchmark", FeedStatus.SIMULATED)

    def monitor() -> MarketMonitor:
        return MarketMonitor(
            store,
            calendar,
            CONTRACT,
            monitor_config,
            DEFAULT_STRUCTURE_CONFIG,
            DEFAULT_STRATEGY_CONFIG,
        )

    def ingest() -> int:
        count = 0
        for batch in store.replay_batches(dataset_id, calendar.coverage):
            for candle in batch:
                active.ingest(
                    FeedFrame.from_replay(candle, FeedStatus.SIMULATED, candle.available_at)
                )
                count += 1
        return count

    active = monitor()
    active.set_connection(True, START)
    elapsed["monitor_ingest"], ingested = _measure(ingest)
    elapsed["monitor_restart"], _ = _measure(monitor, repetitions)
    if ingested != session_count * minutes:
        raise RuntimeError("benchmark did not ingest the full synthetic series")

    with TestClient(create_app(store)) as client:
        api = f"/api/runs/{evidence_id}/chart-evidence"

        def evidence(end: datetime) -> object:
            response = client.get(
                api,
                params={"start": (end - 120 * MINUTE).isoformat(), "through": end.isoformat()},
            )
            response.raise_for_status()
            return response.json()

        def chart() -> dict[str, object]:
            response = client.get(f"/api/monitors/{active.monitor_id}/chart?limit=24")
            response.raise_for_status()
            body = response.json()
            return {key: body[key] for key in ("candles", "market_evidence", "paper_evidence")}

        elapsed["historical_chart_early"], early = _measure(
            lambda: evidence(calendar.coverage.start + minutes * MINUTE), repetitions
        )
        elapsed["historical_chart_recent"], recent = _measure(
            lambda: evidence(calendar.coverage.end), repetitions
        )
        elapsed["monitor_chart_recent"], monitored = _measure(chart, repetitions)

    with store.connection() as db:
        market_events = db.execute("SELECT count(*) FROM monitor_market_events").fetchone()[0]
    return {
        "data_kind": "synthetic_benchmark",
        "dataset_id": dataset_id,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "sessions": session_count,
        "minutes_per_session": minutes,
        "ingested_minutes": ingested,
        "repetitions_for_read_and_restart": repetitions,
        "event_counts": {
            "history_evidence": store.describe_structure_run(evidence_id)["event_count"],
            "paper_replay": store.describe_structure_run(paper_id)["event_count"],
            "monitor_market": market_events,
        },
        "seconds": elapsed,
        "projection_sha256": {
            "historical_early": _digest(early),
            "historical_recent": _digest(recent),
            "monitor_recent": _digest(monitored),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--minutes", type=int, default=360)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.sessions <= 250 or not 120 <= args.minutes <= 600 or args.repetitions < 1:
        parser.error("use 1–250 sessions, 120–600 minutes and at least one repetition")
    with TemporaryDirectory(prefix="aurum-synthetic-benchmark-") as temporary:
        print(canonical_json(run(Path(temporary), args.sessions, args.minutes, args.repetitions)))


if __name__ == "__main__":
    main()
