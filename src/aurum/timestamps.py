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
    stamp = datetime.fromisoformat(raw)
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
    utc = stamp.astimezone(UTC)
    if utc.second or utc.microsecond:
        raise ValueError("timestamp must be aligned to a whole UTC minute")
    return utc
