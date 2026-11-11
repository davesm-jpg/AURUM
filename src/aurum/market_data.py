import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from importlib.metadata import version
from io import StringIO
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

from aurum.databento_csv import (
    DATABENTO_CSV_VERSION,
    DATABENTO_OHLCV_COLUMNS,
    normalized_ohlcv_rows,
)
from aurum.exchange_calendar import SessionCalendar
from aurum.instruments import MAX_SQLITE_INTEGER, MGC, MgcContract
from aurum.parquet_rows import parquet_rows
from aurum.providers import FeedStatus, FileHistorySource, HistoricalSource, SourceFormat
from aurum.timestamps import (
    MINUTE,
    TradeTimestamp,
    minute_timestamp,
    source_timezone,
    trade_timestamp,
)

IMPORT_VERSION = 1
TRADE_IMPORT_VERSION = 1
COLUMNS = ("timestamp", "contract", "open", "high", "low", "close", "volume")
TRADE_COLUMNS = ("timestamp", "contract", "sequence", "price", "size")


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
class TradePrint:
    contract: MgcContract
    executed_at: TradeTimestamp
    sequence: int
    price_ticks: int
    size: int

    def __post_init__(self) -> None:
        if not 0 <= self.sequence <= MAX_SQLITE_INTEGER:
            raise ValueError("trade sequence is outside the supported range")
        if not 0 < self.price_ticks <= MAX_SQLITE_INTEGER:
            raise ValueError("trade price is outside the supported range")
        if not 0 < self.size <= MAX_SQLITE_INTEGER:
            raise ValueError("trade size must be a positive integer in the supported range")

    @property
    def minute_open(self) -> datetime:
        return self.executed_at.time.replace(second=0, microsecond=0)

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract.code,
            "executed_at": self.executed_at.isoformat(),
            "sequence": self.sequence,
            "price_ticks": self.price_ticks,
            "size": self.size,
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
    trade_prints: tuple[TradePrint, ...] = ()


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def prepare_csv(path: Path, config: ImportConfig, calendar: SessionCalendar) -> PreparedDataset:
    return prepare_history(FileHistorySource(path, SourceFormat.CSV), config, calendar)


def prepare_trade_csv(
    path: Path, config: ImportConfig, calendar: SessionCalendar
) -> PreparedDataset:
    return prepare_trade_history(FileHistorySource(path, SourceFormat.CSV), config, calendar)


def prepare_trade_history(
    source: HistoricalSource, config: ImportConfig, calendar: SessionCalendar
) -> PreparedDataset:
    if source.format is not SourceFormat.CSV:
        raise ValueError("trade import requires normalized CSV")
    if config.timestamp_label is not TimestampLabel.OPEN:
        raise ValueError("trade-derived candles use minute open labels")
    if calendar.sessions is None:
        raise ValueError("trade print import requires a dated reviewed calendar")
    raw = source.read()
    prints, warnings = _parse_trade_prints(raw, config, calendar)
    candles = tuple(_trade_candles(prints))
    return _prepared_history(source, raw, config, calendar, candles, warnings, prints)


def prepare_history(
    source: HistoricalSource, config: ImportConfig, calendar: SessionCalendar
) -> PreparedDataset:
    raw = source.read()
    if source.format is SourceFormat.CSV:
        rows = csv_rows(raw)
        row_label = "CSV line"
    elif source.format is SourceFormat.DATABENTO_OHLCV_CSV:
        if config.source_timezone != "UTC" or config.timestamp_label is not TimestampLabel.OPEN:
            raise ValueError("Databento minute exports require UTC and open timestamp labels")
        rows = normalized_ohlcv_rows(csv_rows(raw, DATABENTO_OHLCV_COLUMNS), config.contract)
        row_label = "Databento CSV line"
    else:
        rows = parquet_rows(raw, COLUMNS)
        row_label = "Parquet row"
    candles, warnings = _parse_rows(rows, row_label, config, calendar)
    return _prepared_history(source, raw, config, calendar, candles, warnings)


def _prepared_history(
    source: HistoricalSource,
    raw: bytes,
    config: ImportConfig,
    calendar: SessionCalendar,
    candles: tuple[Candle, ...],
    warnings: list[QualityIssue],
    prints: tuple[TradePrint, ...] = (),
) -> PreparedDataset:
    issues = (*warnings, *_gaps(candles, calendar))
    checksum = sha256(raw).hexdigest()
    provenance: dict[str, object] = {
        "dataset_version": IMPORT_VERSION,
        "source_sha256": checksum,
        "format": source.format,
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
    if source.origin is not None:
        provenance["source_origin"] = source.origin
    if source.format is SourceFormat.DATABENTO_OHLCV_CSV:
        provenance.update(
            databento_normalization_version=DATABENTO_CSV_VERSION,
            source_schema="ohlcv-1m",
            source_timestamp_basis="vendor_receive_interval",
            source_empty_interval_policy="omitted_without_price_filling",
        )
    if source.format is not SourceFormat.CSV or source.origin is not None:
        provenance["source_status"] = (
            FeedStatus.SIMULATED
            if config.data_kind is DataKind.SYNTHETIC
            else FeedStatus.HISTORICAL
        )
    if prints:
        provenance.update(
            source_data_type="trade_prints",
            source_timestamp_basis="trade_execution",
            source_timestamp_precision="nanosecond",
            trade_import_version=TRADE_IMPORT_VERSION,
            trade_print_count=len(prints),
            trade_first_at=prints[0].executed_at.isoformat(),
            trade_last_at=prints[-1].executed_at.isoformat(),
        )
    identity = canonical_json(provenance)
    return PreparedDataset(
        sha256(identity.encode()).hexdigest(), checksum, identity, raw, candles, issues, prints
    )


def csv_rows(raw: bytes, columns: tuple[str, ...] = COLUMNS) -> Iterator[tuple[int, list[str]]]:
    reader = csv.reader(StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    if tuple(next(reader, ())) != columns:
        raise ValueError(f"CSV header must be {','.join(columns)}")
    for row in reader:
        yield reader.line_num, row


def _parse_trade_prints(
    raw: bytes, config: ImportConfig, calendar: SessionCalendar
) -> tuple[tuple[TradePrint, ...], list[QualityIssue]]:
    zone = source_timezone(config.source_timezone)
    prints = []
    sequences: set[int] = set()
    latest: tuple[TradeTimestamp, int] | None = None
    out_of_order = 0
    for line, row in csv_rows(raw, TRADE_COLUMNS):
        try:
            if len(row) != len(TRADE_COLUMNS):
                raise ValueError("wrong number of CSV fields")
            stamp, contract, sequence, price, size = row
            if contract != config.contract.code:
                raise ValueError(f"expected contract {config.contract.code}, found {contract!r}")
            if any(
                not value.isascii() or not value.isdigit() or len(value) > 19
                for value in (sequence, size)
            ):
                raise ValueError("trade sequence and size must be unsigned integers")
            trade = TradePrint(
                config.contract,
                trade_timestamp(stamp, zone),
                int(sequence),
                MGC.price_ticks(price),
                int(size),
            )
            if trade.sequence in sequences:
                raise ValueError(f"duplicate trade sequence {trade.sequence}")
            if not calendar.permits(trade.minute_open):
                raise ValueError("trade timestamp falls outside open calendar windows")
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"CSV line {line}: {exc}") from exc
        key = trade.executed_at, trade.sequence
        if latest is not None and key < latest:
            out_of_order += 1
        else:
            latest = key
        sequences.add(trade.sequence)
        prints.append(trade)
    if not prints:
        raise ValueError("trade CSV contains no prints")
    warnings = [QualityIssue("out_of_order_trade_prints", out_of_order)] if out_of_order else []
    return tuple(sorted(prints, key=lambda trade: (trade.executed_at, trade.sequence))), warnings


def _trade_candles(trades: tuple[TradePrint, ...]) -> Iterator[Candle]:
    for opened, members in groupby(trades, key=lambda trade: trade.minute_open):
        yield candle_from_trade_prints(opened, members)


def candle_from_trade_prints(opened: datetime, trades: Iterator[TradePrint]) -> Candle:
    first = next(trades)
    high = low = close = first.price_ticks
    volume = first.size
    for trade in trades:
        close = trade.price_ticks
        high = max(high, close)
        low = min(low, close)
        volume += trade.size
    return Candle(first.contract, opened, first.price_ticks, high, low, close, volume)


def _parse_rows(
    rows: Iterator[tuple[int, list[str]]],
    row_label: str,
    config: ImportConfig,
    calendar: SessionCalendar,
) -> tuple[tuple[Candle, ...], list[QualityIssue]]:
    zone = source_timezone(config.source_timezone)
    candles: dict[datetime, Candle] = {}
    latest: datetime | None = None
    out_of_order = zero_volume = 0
    for line, row in rows:
        try:
            candle = _parse_row(row, config, zone)
            start = candle.opened_at
            if start in candles:
                raise ValueError(f"duplicate candle at {start.isoformat()}")
            if not calendar.permits(start):
                raise ValueError(f"candle {start.isoformat()} falls outside open calendar windows")
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{row_label} {line}: {exc}") from exc
        if latest is not None and start < latest:
            out_of_order += 1
        else:
            latest = start
        zero_volume += candle.volume == 0
        candles[start] = candle
    if not candles:
        raise ValueError(f"{row_label.split()[0]} contains no candles")
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
