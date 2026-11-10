from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import Candle
from aurum.timestamps import MINUTE

AGGREGATION_VERSION = 1


class Timeframe(StrEnum):
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    FOUR_HOURS = "4h"
    DAILY = "1d"

    @property
    def minutes(self) -> int | None:
        return _INTRADAY_MINUTES.get(self)


_INTRADAY_MINUTES = {
    Timeframe.FIVE_MINUTES: 5,
    Timeframe.FIFTEEN_MINUTES: 15,
    Timeframe.ONE_HOUR: 60,
    Timeframe.FOUR_HOURS: 240,
}


def completed_bar_start(
    calendar: SessionCalendar, timeframe: Timeframe, through: datetime, count: int
) -> datetime | None:
    """Find the earliest open among the last complete bars in a continuous input prefix."""
    if count < 1:
        raise ValueError("completed bar lookback must be positive")
    through = min(through, calendar.coverage.end)
    coverage_start = calendar.coverage.start
    earliest = None
    minutes = timeframe.minutes
    for session in reversed(calendar.sessions or ()):
        if minutes is None:
            if coverage_start <= session.start and session.end <= through:
                earliest = session.start
                count -= 1
                if not count:
                    return earliest
            continue
        for window in reversed(session.windows):
            left, right = max(window.start, coverage_start), min(window.end, through)
            if left >= right:
                continue
            start_offset = (left - window.start) // MINUTE
            end_offset = (right - window.start) // MINUTE
            first = (start_offset + minutes - 1) // minutes
            last = (end_offset + (minutes - 1 if right == window.end else 0)) // minutes
            complete = last - first
            if complete <= 0:
                continue
            width = minutes * MINUTE
            if count <= complete:
                return window.start + (last - count) * width
            earliest = window.start + first * width
            count -= complete
    return earliest


@dataclass(frozen=True, slots=True)
class AggregateCandle:
    contract: MgcContract
    trade_date: date
    timeframe: Timeframe
    opened_at: datetime
    available_at: datetime
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int
    minute_count: int
    shortened: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract.code,
            "trade_date": self.trade_date.isoformat(),
            "timeframe": self.timeframe.value,
            "opened_at": self.opened_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "open_ticks": self.open_ticks,
            "high_ticks": self.high_ticks,
            "low_ticks": self.low_ticks,
            "close_ticks": self.close_ticks,
            "volume": self.volume,
            "minute_count": self.minute_count,
            "shortened": self.shortened,
            "aggregation_version": AGGREGATION_VERSION,
        }


@dataclass(slots=True)
class _Bucket:
    start: datetime
    end: datetime
    expected: int
    open_ticks: int
    high_ticks: int
    low_ticks: int
    volume: int = 0
    count: int = 0

    @classmethod
    def for_candle(
        cls, candle: Candle, session: TradingSession, window: TradingWindow, timeframe: Timeframe
    ) -> Self:
        minutes = timeframe.minutes
        if minutes is None:
            start, end = session.start, session.end
            expected = sum((part.end - part.start) // MINUTE for part in session.windows)
        else:
            width = MINUTE * minutes
            index = (candle.opened_at - window.start) // width
            start = window.start + width * index
            end = start + min(width, window.end - start)
            expected = (end - start) // MINUTE
        return cls(start, end, expected, candle.open_ticks, candle.high_ticks, candle.low_ticks)

    def add(self, candle: Candle) -> None:
        self.high_ticks = max(self.high_ticks, candle.high_ticks)
        self.low_ticks = min(self.low_ticks, candle.low_ticks)
        self.volume += candle.volume
        self.count += 1


class CandleAggregator:
    """Consume validated, chronological minute closes for one dated contract."""

    def __init__(
        self,
        calendar: SessionCalendar,
        contract: MgcContract,
        timeframes: tuple[Timeframe, ...] = tuple(Timeframe),
    ) -> None:
        if calendar.sessions is None:
            raise ValueError("aggregation requires a dated calendar with schema_version 2")
        if not timeframes or len(set(timeframes)) != len(timeframes):
            raise ValueError("select at least one timeframe without duplicates")
        self._calendar = calendar
        self._contract = contract
        self._timeframes = timeframes
        self._last_open: datetime | None = None
        self._buckets: dict[Timeframe, _Bucket] = {}

    def consume(self, candles: Iterable[Candle]) -> Iterator[AggregateCandle]:
        for candle in candles:
            yield from self.push(candle)

    def push(self, candle: Candle) -> tuple[AggregateCandle, ...]:
        if candle.contract != self._contract:
            raise ValueError("cannot aggregate candles from a different contract")
        if self._last_open is not None and candle.opened_at <= self._last_open:
            raise ValueError("minute candles must arrive in strictly chronological order")
        session = self._calendar.session_at(candle.opened_at)
        if session is None:
            raise ValueError("minute candle falls outside covered trading windows")
        window = next(part for part in session.windows if part.start <= candle.opened_at < part.end)

        completed: list[AggregateCandle] = []
        for timeframe in self._timeframes:
            bucket = self._buckets.get(timeframe)
            if bucket is None or candle.opened_at >= bucket.end:
                bucket = _Bucket.for_candle(candle, session, window, timeframe)
                self._buckets[timeframe] = bucket
            bucket.add(candle)
            if candle.available_at == bucket.end and bucket.count == bucket.expected:
                minutes = timeframe.minutes
                completed.append(
                    AggregateCandle(
                        candle.contract,
                        session.trade_date,
                        timeframe,
                        bucket.start,
                        bucket.end,
                        bucket.open_ticks,
                        bucket.high_ticks,
                        bucket.low_ticks,
                        candle.close_ticks,
                        bucket.volume,
                        bucket.count,
                        minutes is not None and bucket.expected < minutes,
                    )
                )
        self._last_open = candle.opened_at
        return tuple(completed)
