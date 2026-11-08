import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from importlib.metadata import version
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from aurum.exchange_calendar import SessionCalendar
from aurum.instruments import MAX_SQLITE_INTEGER, MGC, MgcContract
from aurum.timestamps import MINUTE, minute_timestamp, source_timezone

IMPORT_VERSION = 1
COLUMNS = ("timestamp", "contract", "open", "high", "low", "close", "volume")


class DataKind(StrEnum):
    HISTORICAL = "historical"
    SYNTHETIC = "synthetic"


class TimestampLabel(StrEnum):
    OPEN = "open"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class ImportConfig:
    provider: str
    contract: MgcContract
    source_timezone: str
    timestamp_label: TimestampLabel
    data_kind: DataKind

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider must be provided")
        source_timezone(self.source_timezone)


@dataclass(frozen=True, slots=True)
class Candle:
    contract: MgcContract
    opened_at: datetime
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int

    def __post_init__(self) -> None:
        if self.opened_at.tzinfo is not UTC or self.opened_at.second or self.opened_at.microsecond:
            raise ValueError("candle open must be a whole UTC minute")
        if not (
            0
            < self.low_ticks
            <= min(self.open_ticks, self.close_ticks)
            <= max(self.open_ticks, self.close_ticks)
            <= self.high_ticks
            <= MAX_SQLITE_INTEGER
        ):
            raise ValueError("invalid OHLC bounds")
        if not 0 <= self.volume <= MAX_SQLITE_INTEGER:
            raise ValueError("volume must be a nonnegative integer in the supported range")

    @property
    def available_at(self) -> datetime:
        return self.opened_at + MINUTE

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract.code,
            "opened_at": self.opened_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "open_ticks": self.open_ticks,
            "high_ticks": self.high_ticks,
            "low_ticks": self.low_ticks,
            "close_ticks": self.close_ticks,
            "volume": self.volume,
        }


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: str
    count: int
    start: str | None = None
    end: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedDataset:
    dataset_id: str
    source_sha256: str
    provenance_json: str
    raw: bytes
    candles: tuple[Candle, ...]
    issues: tuple[QualityIssue, ...]


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def prepare_csv(path: Path, config: ImportConfig, calendar: SessionCalendar) -> PreparedDataset:
    raw = path.read_bytes()
    candles, warnings = _parse_csv(raw, config, calendar)
    issues = (*warnings, *_gaps(candles, calendar))
    checksum = sha256(raw).hexdigest()
    identity = canonical_json(
        {
            "dataset_version": IMPORT_VERSION,
            "source_sha256": checksum,
            "format": "csv",
            "data_type": "ohlcv",
            "instrument": MGC.symbol,
            "contract": config.contract.code,
            "price_basis": "unadjusted",
            "timeframe_minutes": 1,
            "tick_size": str(MGC.tick_size),
            "provider": config.provider,
            "source_timezone": config.source_timezone,
            "tzdata_version": version("tzdata"),
            "timestamp_label": config.timestamp_label,
            "data_kind": config.data_kind,
            "calendar": calendar.as_dict(),
        }
    )
    return PreparedDataset(
        sha256(identity.encode()).hexdigest(), checksum, identity, raw, candles, issues
    )


def _parse_csv(
    raw: bytes, config: ImportConfig, calendar: SessionCalendar
) -> tuple[tuple[Candle, ...], list[QualityIssue]]:
    reader = csv.reader(StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    if tuple(next(reader, ())) != COLUMNS:
        raise ValueError(f"CSV header must be {','.join(COLUMNS)}")
    zone = source_timezone(config.source_timezone)
    candles: dict[datetime, Candle] = {}
    latest: datetime | None = None
    out_of_order = zero_volume = 0
    for row in reader:
        try:
            candle = _parse_row(row, config, zone)
            start = candle.opened_at
            if start in candles:
                raise ValueError(f"duplicate candle at {start.isoformat()}")
            if not calendar.permits(start):
                raise ValueError(f"candle {start.isoformat()} falls outside open calendar windows")
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"CSV line {reader.line_num}: {exc}") from exc
        if latest is not None and start < latest:
            out_of_order += 1
        else:
            latest = start
        zero_volume += candle.volume == 0
        candles[start] = candle
    if not candles:
        raise ValueError("CSV contains no candles")
    warnings = [
        QualityIssue(code, count)
        for code, count in (("out_of_order", out_of_order), ("zero_volume", zero_volume))
        if count
    ]
    return tuple(sorted(candles.values(), key=lambda candle: candle.opened_at)), warnings


def _parse_row(row: list[str], config: ImportConfig, zone: ZoneInfo) -> Candle:
    if len(row) != len(COLUMNS):
        raise ValueError("wrong number of CSV fields")
    timestamp, contract, open_, high, low, close, volume = row
    if contract != config.contract.code:
        raise ValueError(f"expected contract {config.contract.code}, found {contract!r}")
    start = minute_timestamp(timestamp, zone)
    if config.timestamp_label == TimestampLabel.CLOSE:
        start -= MINUTE
    if not volume.isascii() or not volume.isdigit() or len(volume) > 19:
        raise ValueError("volume must be a nonnegative integer in the supported range")
    return Candle(
        config.contract,
        start,
        MGC.price_ticks(open_),
        MGC.price_ticks(high),
        MGC.price_ticks(low),
        MGC.price_ticks(close),
        int(volume),
    )


def _gaps(candles: tuple[Candle, ...], calendar: SessionCalendar) -> Iterator[QualityIssue]:
    opens = iter(candle.opened_at for candle in candles)
    current = next(opens, None)
    for window in calendar.windows:
        expected = window.start
        while current is not None and current < window.end:
            if expected < current:
                yield _missing(expected, current)
            expected = current + MINUTE
            current = next(opens, None)
        if expected < window.end:
            yield _missing(expected, window.end)


def _missing(start: datetime, end: datetime) -> QualityIssue:
    return QualityIssue(
        "missing_candles", (end - start) // MINUTE, start.isoformat(), end.isoformat()
    )
