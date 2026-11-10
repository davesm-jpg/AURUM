from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from importlib.resources import files
from re import fullmatch
from zoneinfo import ZoneInfo

MINUTE = timedelta(minutes=1)


@lru_cache(maxsize=64)
def source_timezone(name: str) -> ZoneInfo:
    parts = name.split("/")
    if all(fullmatch(r"[A-Za-z0-9_+-]+", part) for part in parts):
        try:
            with files("tzdata.zoneinfo").joinpath(*parts).open("rb") as stream:
                return ZoneInfo.from_file(stream, key=name)
        except (FileNotFoundError, IsADirectoryError, ValueError):
            pass
    raise ValueError(f"unknown source timezone: {name}")


def minute_timestamp(raw: str, source_zone: ZoneInfo | None = None) -> datetime:
    if (
        fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::00(?:\.0{1,6})?)?"
            r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?",
            raw,
        )
        is None
    ):
        raise ValueError("timestamp must be an ISO 8601 date and time on a whole UTC minute")
    utc = _normalized_utc(datetime.fromisoformat(raw), source_zone)
    if utc.second or utc.microsecond:
        raise ValueError("timestamp must be aligned to a whole UTC minute")
    return utc


@dataclass(frozen=True, slots=True, order=True)
class TradeTimestamp:
    time: datetime
    nanosecond_tail: int = 0

    def __post_init__(self) -> None:
        if self.time.tzinfo is not UTC or not 0 <= self.nanosecond_tail <= 999:
            raise ValueError("trade timestamp must use UTC and exact nanosecond precision")

    def isoformat(self) -> str:
        micros = self.time.isoformat(timespec="microseconds").removesuffix("+00:00")
        return f"{micros}{self.nanosecond_tail:03d}+00:00"


def trade_timestamp(raw: str, source_zone: ZoneInfo | None = None) -> TradeTimestamp:
    matched = fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?P<fraction>\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?",
        raw,
    )
    if matched is None:
        raise ValueError("trade timestamp must include seconds and at most nine fractional digits")
    fraction = matched["fraction"] or ""
    tail = int(fraction[7:].ljust(3, "0"))
    return TradeTimestamp(_normalized_utc(datetime.fromisoformat(raw), source_zone), tail)


def _normalized_utc(stamp: datetime, source_zone: ZoneInfo | None) -> datetime:
    if stamp.tzinfo is None:
        if source_zone is None:
            raise ValueError("timestamp must include a UTC offset")
        candidates = set()
        for fold in (0, 1):
            utc = stamp.replace(tzinfo=source_zone, fold=fold).astimezone(UTC)
            if utc.astimezone(source_zone).replace(tzinfo=None) == stamp:
                candidates.add(utc)
        if len(candidates) != 1:
            raise ValueError("local timestamp is ambiguous or nonexistent")
        stamp = candidates.pop()
    elif source_zone is not None:
        if stamp.utcoffset() != stamp.astimezone(source_zone).utcoffset():
            raise ValueError("timestamp offset disagrees with source timezone")
    return stamp.astimezone(UTC)
