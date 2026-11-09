from bisect import bisect_right
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from aurum.candles import AggregateCandle
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.liquidity import LevelKind, LiquidityLevel
from aurum.market_data import Candle
from aurum.market_structure import Scope, Side, Swing
from aurum.sessions import ASIA_HOURS, LONDON_HOURS, SessionHours
from aurum.timestamps import MINUTE, source_timezone


@dataclass(frozen=True, slots=True)
class LiquidityMapConfig:
    swing_scope: Scope = Scope.INTERNAL
    equal_tolerance_ticks: int = 1
    swing_history: int = 12
    range_bars: int = 20
    asia_hours: SessionHours = ASIA_HOURS
    london_hours: SessionHours = LONDON_HOURS

    def __post_init__(self) -> None:
        if self.equal_tolerance_ticks < 0 or self.swing_history < 3 or self.range_bars < 2:
            raise ValueError("invalid liquidity mapping tolerance or lookback")


DEFAULT_LIQUIDITY_MAP_CONFIG = LiquidityMapConfig()


@dataclass(slots=True)
class _Extremes:
    high: tuple[int, datetime] | None = None
    low: tuple[int, datetime] | None = None
    count: int = 0

    def push(self, bar: Candle | AggregateCandle) -> None:
        if self.high is None or bar.high_ticks > self.high[0]:
            self.high = (bar.high_ticks, bar.opened_at)
        if self.low is None or bar.low_ticks < self.low[0]:
            self.low = (bar.low_ticks, bar.opened_at)
        self.count += 1


@dataclass(frozen=True, slots=True)
class _Origin:
    kind: LevelKind
    start: datetime
    end: datetime
    timeframe: str
    extremes: _Extremes

    def levels(self, bar: AggregateCandle) -> Iterator[LiquidityLevel]:
        for side, value in ((Side.HIGH, self.extremes.high), (Side.LOW, self.extremes.low)):
            if value is not None:
                price, opened = value
                yield LiquidityLevel(
                    bar.contract,
                    self.kind,
                    side,
                    price,
                    bar.available_at,
                    self.start,
                    self.end,
                    self.timeframe,
                    (opened,),
                )


@dataclass(slots=True)
class _SessionObservation:
    window: TradingWindow
    expected: int
    extremes: _Extremes = field(default_factory=_Extremes)


class _SessionCollector:
    def __init__(self, kind: LevelKind, hours: SessionHours, calendar: SessionCalendar) -> None:
        self.kind = kind
        self.hours = hours
        self.calendar = calendar
        self._active: _SessionObservation | None = None

    def push(self, candle: Candle) -> Iterator[_Origin]:
        if (
            self._active is not None
            and candle.opened_at >= self._active.window.end
            and (completed := self._finish()) is not None
        ):
            yield completed
        window = self.hours.window_at(candle.opened_at)
        if window is None:
            return
        if self._active is None:
            self._active = _SessionObservation(window, _expected_minutes(self.calendar, window))
        self._active.extremes.push(candle)
        if candle.available_at >= window.end and (completed := self._finish()) is not None:
            yield completed

    def _finish(self) -> _Origin | None:
        active = self._active
        self._active = None
        if active is None or not active.expected or active.extremes.count != active.expected:
            return None
        return _Origin(self.kind, active.window.start, active.window.end, "1m", active.extremes)


class LiquidityMapper:
    """Build proxies from confirmed swings, closed bars and completed time windows."""

    def __init__(
        self, calendar: SessionCalendar, config: LiquidityMapConfig = DEFAULT_LIQUIDITY_MAP_CONFIG
    ) -> None:
        self.config = config
        self._swings: dict[Side, deque[Swing]] = {
            side: deque(maxlen=config.swing_history) for side in Side
        }
        self._range: deque[AggregateCandle] = deque(maxlen=config.range_bars)
        self._range_prices: dict[Side, int] = {}
        self._periods = (
            _SessionCollector(LevelKind.ASIA, config.asia_hours, calendar),
            _SessionCollector(LevelKind.LONDON, config.london_hours, calendar),
        )
        self._pending: dict[LevelKind, _Origin] = {}
        self._weeks = _complete_week_dates(calendar)
        self._week_dates: tuple[date, ...] = ()
        self._week_extremes = _Extremes()
        self._week_start: datetime | None = None

    def push_minute(self, candle: Candle) -> None:
        for collector in self._periods:
            for origin in collector.push(candle):
                self._pending[origin.kind] = origin

    def push_daily(self, daily: AggregateCandle) -> None:
        extremes = _Extremes()
        extremes.push(daily)
        self._pending[LevelKind.PREVIOUS_DAY] = _Origin(
            LevelKind.PREVIOUS_DAY, daily.opened_at, daily.available_at, daily.timeframe, extremes
        )

        planned = self._weeks.get(daily.trade_date)
        if planned is None:
            return
        if self._week_dates != planned:
            self._week_dates = planned
            self._week_extremes = _Extremes()
            self._week_start = daily.opened_at
        self._week_extremes.push(daily)
        if daily.trade_date == planned[-1] and self._week_extremes.count == len(planned):
            assert self._week_start is not None
            self._pending[LevelKind.PREVIOUS_WEEK] = _Origin(
                LevelKind.PREVIOUS_WEEK,
                self._week_start,
                daily.available_at,
                daily.timeframe,
                self._week_extremes,
            )

    def release(self, bar: AggregateCandle, swings: Iterable[Swing]) -> Iterator[LiquidityLevel]:
        for kind, origin in tuple(self._pending.items()):
            if origin.end <= bar.available_at:
                yield from origin.levels(bar)
                del self._pending[kind]
        for swing in swings:
            if swing.scope is self.config.swing_scope:
                yield from self._confirmed_swing(bar, swing)
        yield from self._range_levels(bar)

    def _confirmed_swing(self, bar: AggregateCandle, swing: Swing) -> Iterator[LiquidityLevel]:
        yield LiquidityLevel(
            bar.contract,
            LevelKind.SWING,
            swing.side,
            swing.price_ticks,
            swing.confirmed_at,
            swing.pivot_opened_at,
            swing.confirmed_at,
            bar.timeframe,
            (swing.pivot_opened_at,),
        )
        history = self._swings[swing.side]
        matches = [
            other
            for other in history
            if abs(other.price_ticks - swing.price_ticks) <= self.config.equal_tolerance_ticks
        ]
        history.append(swing)
        if matches:
            matches.append(swing)
            boundary = max if swing.side is Side.HIGH else min
            kind = LevelKind.EQUAL if len(matches) == 2 else LevelKind.REPEATED
            opens = tuple(other.pivot_opened_at for other in matches)
            yield LiquidityLevel(
                bar.contract,
                kind,
                swing.side,
                boundary(other.price_ticks for other in matches),
                swing.confirmed_at,
                min(opens),
                swing.confirmed_at,
                bar.timeframe,
                opens,
                len(matches),
            )

    def _range_levels(self, bar: AggregateCandle) -> Iterator[LiquidityLevel]:
        self._range.append(bar)
        if len(self._range) < self.config.range_bars:
            return
        high = max(self._range, key=lambda item: item.high_ticks)
        low = min(self._range, key=lambda item: item.low_ticks)
        for side, price, source in (
            (Side.HIGH, high.high_ticks, high),
            (Side.LOW, low.low_ticks, low),
        ):
            if self._range_prices.get(side) == price:
                continue
            self._range_prices[side] = price
            yield LiquidityLevel(
                bar.contract,
                LevelKind.RANGE,
                side,
                price,
                bar.available_at,
                self._range[0].opened_at,
                bar.available_at,
                bar.timeframe,
                (source.opened_at,),
            )


def _expected_minutes(calendar: SessionCalendar, window: TradingWindow) -> int:
    if window.start < calendar.coverage.start or window.end > calendar.coverage.end:
        return 0
    windows = calendar.windows
    index = bisect_right(windows, window.start, key=lambda span: span.end)
    count = 0
    while index < len(windows) and windows[index].start < window.end:
        span = windows[index]
        count += (min(span.end, window.end) - max(span.start, window.start)) // MINUTE
        index += 1
    return count


def _complete_week_dates(calendar: SessionCalendar) -> dict[date, tuple[date, ...]]:
    groups: dict[date, list[date]] = defaultdict(list)
    for session in calendar.sessions or ():
        day = session.trade_date
        groups[day - timedelta(days=day.weekday())].append(day)
    zone = source_timezone("America/Chicago")
    result: dict[date, tuple[date, ...]] = {}
    for monday, days in groups.items():
        start = datetime.combine(monday - timedelta(days=1), time.min, zone).astimezone(UTC)
        end = datetime.combine(monday + timedelta(days=6), time.min, zone).astimezone(UTC)
        if calendar.coverage.start <= start and calendar.coverage.end >= end:
            expected = tuple(days)
            result.update((day, expected) for day in expected)
    return result
