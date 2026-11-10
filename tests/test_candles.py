import json
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from sys import executable

import pytest

from aurum.candles import AggregateCandle, CandleAggregator, Timeframe, completed_bar_start
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import Candle, DataKind, ImportConfig, TimestampLabel, prepare_csv
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp

CONTRACT = MgcContract("MGC-2025-04")
TRADE_DATE = date(2025, 3, 10)


def window(start: str, end: str) -> TradingWindow:
    return TradingWindow(minute_timestamp(start), minute_timestamp(end))


def synthetic_calendar(
    *windows: TradingWindow, coverage: TradingWindow | None = None
) -> SessionCalendar:
    return SessionCalendar(
        "synthetic aggregation test",
        "fixture-v1",
        coverage or TradingWindow(windows[0].start, windows[-1].end),
        sessions=(TradingSession(TRADE_DATE, windows),),
    )


def synthetic_bars(calendar: SessionCalendar) -> Iterator[Candle]:
    price = 29000
    for span in calendar.windows:
        opened = span.start
        while opened < span.end:
            yield Candle(CONTRACT, opened, price, price + 3, price - 2, price + 1, 2)
            opened += MINUTE
            price += 1


def test_ohlcv_and_close_availability() -> None:
    calendar = synthetic_calendar(window("2025-03-10T13:00Z", "2025-03-10T13:15Z"))
    aggregator = CandleAggregator(
        calendar, CONTRACT, (Timeframe.FIVE_MINUTES, Timeframe.FIFTEEN_MINUTES)
    )
    bars = list(synthetic_bars(calendar))
    for candle in bars[:4]:
        assert aggregator.push(candle) == ()
    (first,) = aggregator.push(bars[4])
    assert first == AggregateCandle(
        CONTRACT,
        TRADE_DATE,
        Timeframe.FIVE_MINUTES,
        bars[0].opened_at,
        bars[4].available_at,
        29000,
        29007,
        28998,
        29005,
        10,
        5,
        False,
    )

    completed = [bar for candle in bars[5:] for bar in aggregator.push(candle)]
    assert [bar.timeframe for bar in completed] == [
        Timeframe.FIVE_MINUTES,
        Timeframe.FIVE_MINUTES,
        Timeframe.FIFTEEN_MINUTES,
    ]
    assert completed[-1].minute_count == 15
    assert completed[-1].available_at == bars[-1].available_at
    assert completed[-1].volume == 30


def test_halt_restarts_intraday_bucket_and_daily_combines_all_open_minutes() -> None:
    calendar = synthetic_calendar(
        window("2025-03-10T13:00Z", "2025-03-10T13:07Z"),
        window("2025-03-10T13:09Z", "2025-03-10T13:15Z"),
    )
    aggregator = CandleAggregator(calendar, CONTRACT, (Timeframe.FIVE_MINUTES, Timeframe.DAILY))
    completed = [bar for candle in synthetic_bars(calendar) for bar in aggregator.push(candle)]
    assert [
        (bar.opened_at.minute, bar.available_at.minute, bar.minute_count, bar.shortened)
        for bar in completed
    ] == [
        (0, 5, 5, False),
        (5, 7, 2, True),
        (9, 14, 5, False),
        (14, 15, 1, True),
        (0, 15, 13, False),
    ]
    assert completed[-1].timeframe == Timeframe.DAILY
    assert completed[-1].volume == 26


def test_partial_export_and_missing_minutes_do_not_publish_incomplete_buckets() -> None:
    full = window("2025-03-10T13:00Z", "2025-03-10T13:16Z")
    clipped = synthetic_calendar(full, coverage=window("2025-03-10T13:01Z", "2025-03-10T13:14Z"))
    aggregate = CandleAggregator(clipped, CONTRACT, (Timeframe.FIVE_MINUTES, Timeframe.DAILY))
    (complete,) = [bar for candle in synthetic_bars(clipped) for bar in aggregate.push(candle)]
    assert complete.opened_at.minute == 5
    assert complete.available_at.minute == 10

    calendar = synthetic_calendar(full)
    aggregate = CandleAggregator(calendar, CONTRACT, (Timeframe.FIVE_MINUTES, Timeframe.DAILY))
    completed = [
        bar
        for candle in synthetic_bars(calendar)
        if candle.opened_at.minute not in {2, 9}
        for bar in aggregate.push(candle)
    ]
    assert [bar.opened_at.minute for bar in completed] == [10, 15]
    assert completed[-1].shortened


@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_completed_bar_lookback_matches_synthetic_aggregation_across_clips(
    timeframe: Timeframe,
) -> None:
    monday = window("2025-03-10T13:00Z", "2025-03-10T17:13Z")
    tuesday = window("2025-03-11T13:00Z", "2025-03-11T13:07Z")
    tuesday_late = window("2025-03-11T13:11Z", "2025-03-11T17:13Z")
    wednesday = window("2025-03-12T13:00Z", "2025-03-12T17:13Z")
    coverage = TradingWindow(monday.start + MINUTE, wednesday.end - 2 * MINUTE)
    calendar = SessionCalendar(
        "synthetic lookback fixture",
        "synthetic-v1",
        coverage,
        sessions=(
            TradingSession(monday.start.date(), (monday,)),
            TradingSession(tuesday.start.date(), (tuesday, tuesday_late)),
            TradingSession(wednesday.start.date(), (wednesday,)),
        ),
    )
    bars = list(
        CandleAggregator(calendar, CONTRACT, (timeframe,)).consume(synthetic_bars(calendar))
    )
    boundaries = (coverage.start, tuesday.end, tuesday_late.start, tuesday_late.end, coverage.end)
    for through in boundaries:
        seen = [item for item in bars if item.available_at <= through]
        for count in (1, 2, 7, 1000):
            expected = seen[-min(len(seen), count)].opened_at if seen else None
            assert completed_bar_start(calendar, timeframe, through, count) == expected


def test_rejected_input_leaves_state_available_for_correct_next_candle() -> None:
    calendar = synthetic_calendar(window("2025-03-10T13:00Z", "2025-03-10T13:02Z"))
    first, second = synthetic_bars(calendar)
    aggregator = CandleAggregator(calendar, CONTRACT, (Timeframe.FIVE_MINUTES,))
    assert aggregator.push(first) == ()
    with pytest.raises(ValueError, match="strictly chronological"):
        aggregator.push(first)
    with pytest.raises(ValueError, match="different contract"):
        aggregator.push(Candle(MgcContract("MGC-2025-06"), second.opened_at, 3, 3, 3, 3, 0))
    with pytest.raises(ValueError, match="outside covered"):
        aggregator.push(Candle(CONTRACT, second.available_at, 3, 3, 3, 3, 0))
    (completed,) = aggregator.push(second)
    assert completed.minute_count == 2
    with pytest.raises(ValueError, match="strictly chronological"):
        aggregator.push(first)


def test_legacy_calendar_is_rejected_for_aggregation() -> None:
    coverage = window("2025-03-10T13:00Z", "2025-03-10T13:15Z")
    legacy = SessionCalendar("synthetic fixture", "fixture-v1", coverage, (coverage,))
    with pytest.raises(ValueError, match="dated calendar"):
        CandleAggregator(legacy, CONTRACT)


def test_four_hour_alignment_preserves_sunday_open_at_dst_change() -> None:
    calendar = synthetic_calendar(
        window("2025-03-09T17:00-05:00", "2025-03-10T16:00-05:00"),
        coverage=window("2025-03-09T17:00-05:00", "2025-03-10T02:01Z"),
    )
    aggregator = CandleAggregator(calendar, CONTRACT, (Timeframe.FOUR_HOURS, Timeframe.DAILY))
    (completed,) = [bar for candle in synthetic_bars(calendar) for bar in aggregator.push(candle)]
    assert completed.opened_at == minute_timestamp("2025-03-09T22:00Z")
    assert completed.available_at == minute_timestamp("2025-03-10T02:00Z")
    assert completed.trade_date == TRADE_DATE
    assert completed.minute_count == 240


def test_cli_aggregation_matches_incremental_synthetic_feed(tmp_path: Path) -> None:
    calendar = synthetic_calendar(window("2025-03-10T13:00Z", "2025-03-10T14:05Z"))
    bars = list(synthetic_bars(calendar))
    csv_file = tmp_path / "synthetic.csv"
    with csv_file.open("w", encoding="utf-8") as file:
        file.write("timestamp,contract,open,high,low,close,volume\n")
        for bar in bars:
            file.write(
                f"{bar.opened_at.isoformat()},{CONTRACT.code},{bar.open_ticks / 10:.1f},"
                f"{bar.high_ticks / 10:.1f},{bar.low_ticks / 10:.1f},{bar.close_ticks / 10:.1f},2\n"
            )
    config = ImportConfig(
        "synthetic test", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC
    )
    db = tmp_path / "fixture.sqlite"
    dataset = DatasetStore(db).save(prepare_csv(csv_file, config, calendar))
    args = [str(Path(executable).with_name("aurum")), "--db", str(db), "aggregate", dataset]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    aggregator = CandleAggregator(calendar, CONTRACT)
    expected = [bar.as_dict() for candle in bars for bar in aggregator.push(candle)]
    assert {bar["timeframe"] for bar in expected} == {"5m", "15m", "1h", "4h", "1d"}
    assert len({event["event_id"] for event in events}) == len(expected)
    for event in events:
        assert event.pop("event") == "aggregate_closed"
        assert event.pop("dataset_id") == dataset
        assert event.pop("data_kind") == "synthetic"
        assert event.pop("event_id").startswith(f"{dataset}/aggregate-v1/")
    assert events == expected

    result = subprocess.run(
        [*args, "--timeframe", "1h"], capture_output=True, text=True, check=True
    )
    assert [json.loads(line)["timeframe"] for line in result.stdout.splitlines()] == ["1h", "1h"]
