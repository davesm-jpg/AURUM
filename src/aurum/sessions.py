from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from aurum.exchange_calendar import TradingWindow
from aurum.timestamps import minute_timestamp, source_timezone


@dataclass(frozen=True, slots=True)
class SessionHours:
    timezone: str
    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        source_timezone(self.timezone)
        if (
            not 0 <= self.start_minute < 1440
            or not 0 <= self.end_minute < 1440
            or self.start_minute == self.end_minute
        ):
            raise ValueError("session hours require distinct local minutes from 0 to 1439")

    def window_at(self, stamp: datetime) -> TradingWindow | None:
        local = stamp.astimezone(source_timezone(self.timezone))
        minute = local.hour * 60 + local.minute
        day = local.date()
        if self.start_minute < self.end_minute:
            if not self.start_minute <= minute < self.end_minute:
                return None
        elif minute < self.end_minute:
            day -= timedelta(days=1)
        elif minute < self.start_minute:
            return None
        return _window_for(self.timezone, self.start_minute, self.end_minute, day)


@lru_cache(maxsize=64)
def _window_for(timezone: str, start_minute: int, end_minute: int, day: date) -> TradingWindow:
    last_day = day
    if end_minute < start_minute:
        last_day += timedelta(days=1)
    zone = source_timezone(timezone)
    start = datetime.combine(day, _local_time(start_minute))
    end = datetime.combine(last_day, _local_time(end_minute))
    return TradingWindow(
        minute_timestamp(start.isoformat(), zone), minute_timestamp(end.isoformat(), zone)
    )


def _local_time(minute: int) -> time:
    hour, remainder = divmod(minute, 60)
    return time(hour, remainder)


ASIA_HOURS = SessionHours("Asia/Tokyo", 9 * 60, 15 * 60)
LONDON_HOURS = SessionHours("Europe/London", 8 * 60, 12 * 60)
