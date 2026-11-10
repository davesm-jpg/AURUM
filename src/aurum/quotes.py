"""Immutable historical best bid and ask observations, separate from fills and candles."""

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.instruments import MAX_SQLITE_INTEGER, MGC, MgcContract
from aurum.market_data import DataKind, QualityIssue, canonical_json, csv_rows
from aurum.storage import DatasetStore
from aurum.timestamps import EventTimestamp, event_timestamp, source_timezone

QUOTE_IMPORT_VERSION = 1
QUOTE_LOOKUP_VERSION = 1
QUOTE_COLUMNS = ("observed_at", "contract", "sequence", "bid", "ask", "bid_size", "ask_size")
DEFAULT_QUOTE_MAX_AGE_MS = 1000
MAX_QUOTE_AGE_MS = 86_400_000


class QuoteStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    NOT_OBSERVED = "not_observed"
    CLOSED = "market_closed"
    UNCOVERED = "calendar_unavailable"


@dataclass(frozen=True, slots=True)
class QuoteImportConfig:
    provider: str
    contract: MgcContract
    source_timezone: str
    data_kind: DataKind

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("quote provider must be provided")
        source_timezone(self.source_timezone)


@dataclass(frozen=True, slots=True)
class BookQuote:
    observed_at: EventTimestamp
    sequence: int
    bid_ticks: int
    ask_ticks: int
    bid_size: int | None
    ask_size: int | None

    def __post_init__(self) -> None:
        if not 0 <= self.sequence <= MAX_SQLITE_INTEGER:
            raise ValueError("quote sequence is outside the supported range")
        if not 0 < self.bid_ticks <= self.ask_ticks <= MAX_SQLITE_INTEGER:
            raise ValueError("best bid and ask must be positive and uncrossed")
        if any(
            size is not None and not 0 <= size <= MAX_SQLITE_INTEGER
            for size in (self.bid_size, self.ask_size)
        ):
            raise ValueError("quote size is outside the supported range")

    def as_dict(self) -> dict[str, object]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "sequence": self.sequence,
            "bid_ticks": self.bid_ticks,
            "ask_ticks": self.ask_ticks,
            "spread_ticks": self.ask_ticks - self.bid_ticks,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
        }


def _unsigned(value: str) -> int:
    if not value.isascii() or not value.isdigit() or len(value) > 19:
        raise ValueError("quote sequence and reported sizes must be unsigned integers")
    return int(value)


def _normalize(
    raw: bytes, config: QuoteImportConfig, calendar: SessionCalendar
) -> tuple[list[BookQuote], list[QualityIssue]]:
    zone = source_timezone(config.source_timezone)
    quotes = []
    sequences: set[int] = set()
    latest: tuple[EventTimestamp, int] | None = None
    out_of_order = locked = missing_sizes = 0
    for line, row in csv_rows(raw, QUOTE_COLUMNS):
        try:
            if len(row) != len(QUOTE_COLUMNS):
                raise ValueError("wrong number of CSV fields")
            stamp, contract, seq, bid, ask, bid_size, ask_size = row
            if contract != config.contract.code:
                raise ValueError(f"expected contract {config.contract.code}, found {contract!r}")
            quote = BookQuote(
                event_timestamp(stamp, zone),
                _unsigned(seq),
                MGC.price_ticks(bid),
                MGC.price_ticks(ask),
                _unsigned(bid_size) if bid_size else None,
                _unsigned(ask_size) if ask_size else None,
            )
            if quote.sequence in sequences:
                raise ValueError(f"duplicate quote sequence {quote.sequence}")
            minute = quote.observed_at.time.replace(second=0, microsecond=0)
            if not calendar.permits(minute):
                raise ValueError("quote observation falls outside open calendar windows")
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"CSV line {line}: {exc}") from exc
        key = quote.observed_at, quote.sequence
        if latest is not None and key < latest:
            out_of_order += 1
        else:
            latest = key
        sequences.add(quote.sequence)
        locked += quote.bid_ticks == quote.ask_ticks
        missing_sizes += quote.bid_size is None or quote.ask_size is None
        quotes.append(quote)
    if not quotes:
        raise ValueError("quote CSV contains no observations")
    quotes.sort(key=lambda quote: (quote.observed_at, quote.sequence))
    issues = [
        QualityIssue(code, count)
        for code, count in (
            ("out_of_order_quotes", out_of_order),
            ("locked_quotes", locked),
            ("quote_size_unavailable", missing_sizes),
        )
        if count
    ]
    return quotes, issues


def _quote_rows(series_id: str, quotes: Iterable[BookQuote]) -> Iterable[tuple[object, ...]]:
    return (
        (
            series_id,
            quote.observed_at.isoformat(),
            quote.sequence,
            quote.bid_ticks,
            quote.ask_ticks,
            quote.bid_size,
            quote.ask_size,
        )
        for quote in quotes
    )


def import_quote_csv(
    store: DatasetStore, path: Path, config: QuoteImportConfig, calendar: SessionCalendar
) -> str:
    if calendar.sessions is None:
        raise ValueError("quote import requires a dated reviewed calendar")
    raw = path.read_bytes()
    quotes, issues = _normalize(raw, config, calendar)
    checksum = sha256(raw).hexdigest()
    provenance = canonical_json(
        {
            "quote_import_version": QUOTE_IMPORT_VERSION,
            "source_sha256": checksum,
            "format": "csv",
            "data_type": "top_of_book",
            "instrument": MGC.symbol,
            "contract": config.contract.code,
            "price_basis": "unadjusted",
            "tick_size": str(MGC.tick_size),
            "provider": config.provider,
            "source_timezone": config.source_timezone,
            "tzdata_version": version("tzdata"),
            "timestamp_basis": "historical_observation",
            "data_kind": config.data_kind,
            "calendar": calendar.as_dict(),
        }
    )
    series_id = sha256(provenance.encode()).hexdigest()
    with store.connection(write=True) as db:
        db.execute(
            "INSERT INTO source_blobs VALUES (?, ?) ON CONFLICT (sha256) DO NOTHING",
            (checksum, raw),
        )
        saved = db.execute(
            "INSERT INTO quote_series VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (
                series_id,
                checksum,
                provenance,
                datetime.now(UTC).isoformat(),
                quotes[0].observed_at.isoformat(),
                quotes[-1].observed_at.isoformat(),
                len(quotes),
                canonical_json([asdict(issue) for issue in issues]),
            ),
        )
        if saved.rowcount:
            db.executemany(
                "INSERT INTO book_quotes VALUES (?, ?, ?, ?, ?, ?, ?)",
                _quote_rows(series_id, quotes),
            )
    return series_id


def _metadata(row: sqlite3.Row) -> dict[str, object]:
    return {
        "series_id": row["id"],
        "imported_at": row["imported_at"],
        "first_observed_at": row["first_observed_at"],
        "last_observed_at": row["last_observed_at"],
        "quote_count": row["quote_count"],
        "issues": json.loads(row["issues"]),
        "provenance": json.loads(row["provenance"]),
    }


def list_quote_series(store: DatasetStore, limit: int = 100) -> list[dict[str, object]]:
    if not 1 <= limit <= 1000:
        raise ValueError("quote series limit must be between 1 and 1000")
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 13:
            return []
        return [
            _metadata(row)
            for row in db.execute(
                "SELECT * FROM quote_series ORDER BY imported_at DESC, id LIMIT ?", (limit,)
            )
        ]


def describe_quote_series(store: DatasetStore, series_id: str) -> dict[str, object]:
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 13:
            row = db.execute("SELECT * FROM quote_series WHERE id = ?", (series_id,)).fetchone()
            if row is not None:
                return _metadata(row)
    raise ValueError(f"unknown quote series: {series_id}")


def quote_window(
    calendar: SessionCalendar, at: EventTimestamp
) -> tuple[date, TradingWindow] | None:
    minute = at.time.replace(second=0, microsecond=0)
    session = calendar.session_at(minute)
    if session is None:
        return None
    return session.trade_date, next(
        window for window in session.windows if window.start <= minute < window.end
    )


@dataclass(frozen=True, slots=True)
class QuoteAssessment:
    status: QuoteStatus
    quote: BookQuote | None = None
    age_nanoseconds: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "age_nanoseconds": self.age_nanoseconds,
            "quote": self.quote.as_dict() if self.quote is not None else None,
        }


class QuoteLookup:
    def __init__(self, store: DatasetStore, series_id: str) -> None:
        self.store = store
        self.series_id = series_id
        provenance = describe_quote_series(store, series_id)["provenance"]
        assert isinstance(provenance, dict)
        self.provenance = provenance
        self.calendar = SessionCalendar.from_dict(provenance["calendar"])

    def assess_many(
        self, times: Iterable[EventTimestamp], maximum_age_ms: int
    ) -> tuple[QuoteAssessment, ...]:
        if not 0 <= maximum_age_ms <= MAX_QUOTE_AGE_MS:
            raise ValueError("quote maximum age must be between zero and 86400000 ms")
        with self.store.connection() as db:
            return tuple(self._assess(db, at, maximum_age_ms) for at in times)

    def _assess(
        self, db: sqlite3.Connection, at: EventTimestamp, maximum_age_ms: int
    ) -> QuoteAssessment:
        active = quote_window(self.calendar, at)
        if active is None:
            status = (
                QuoteStatus.CLOSED
                if self.calendar.coverage.start <= at.time < self.calendar.coverage.end
                else QuoteStatus.UNCOVERED
            )
            return QuoteAssessment(status)
        _, window = active
        lower = EventTimestamp(max(window.start, self.calendar.coverage.start))
        row = db.execute(
            """SELECT * FROM book_quotes
               WHERE series_id = ? AND observed_at >= ? AND observed_at < ?
               ORDER BY observed_at DESC, sequence DESC LIMIT 1""",
            (self.series_id, lower.isoformat(), at.isoformat()),
        ).fetchone()
        if row is None:
            return QuoteAssessment(QuoteStatus.NOT_OBSERVED)
        observation = BookQuote(
            event_timestamp(row["observed_at"]),
            row["sequence"],
            row["bid_ticks"],
            row["ask_ticks"],
            row["bid_size"],
            row["ask_size"],
        )
        age = at.nanoseconds_since(observation.observed_at)
        status = QuoteStatus.FRESH if age <= maximum_age_ms * 1_000_000 else QuoteStatus.STALE
        return QuoteAssessment(status, observation, age)


def quote_at(
    store: DatasetStore,
    series_id: str,
    at: EventTimestamp,
    maximum_age_ms: int = DEFAULT_QUOTE_MAX_AGE_MS,
) -> dict[str, object]:
    lookup = QuoteLookup(store, series_id)
    provenance = lookup.provenance
    (assessment,) = lookup.assess_many((at,), maximum_age_ms)
    return {
        "quote_lookup_version": QUOTE_LOOKUP_VERSION,
        "series_id": series_id,
        "contract": provenance["contract"],
        "provider": provenance["provider"],
        "data_kind": provenance["data_kind"],
        "as_of": at.isoformat(),
        "maximum_age_ms": maximum_age_ms,
        "eligibility": "strictly_before_as_of_in_same_open_window",
        **assessment.as_dict(),
    }
