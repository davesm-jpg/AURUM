"""Archive explicitly fetched official iCalendar publications and replay their receipt clocks."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from hashlib import sha256
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from aurum.economic_events import EconomicCalendar, EconomicSourceKind
from aurum.economic_ics import (
    MAX_ECONOMIC_CAPTURES,
    MAX_ECONOMIC_ICS_BYTES,
    EconomicIcsInput,
    compare_economic_ics_inputs,
    normalize_economic_ics_input,
    parse_economic_ics,
)
from aurum.exchange_calendar import TradingWindow
from aurum.market_data import canonical_json
from aurum.providers import NoRedirect
from aurum.storage import DatasetStore

ARCHIVE_VERSION = 1
_MAX_ARCHIVE_PAGE = 500


class EconomicPublisher(StrEnum):
    BEA = "bea"
    BLS = "bls"

    @property
    def url(self) -> str:
        return _PUBLICATIONS[self][0]

    @property
    def source(self) -> str:
        return _PUBLICATIONS[self][1]


_PUBLICATIONS = {
    EconomicPublisher.BEA: (
        "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics",
        "U.S. Bureau of Economic Analysis",
    ),
    EconomicPublisher.BLS: (
        "https://www.bls.gov/schedule/news_release/bls.ics",
        "U.S. Bureau of Labor Statistics",
    ),
}


@dataclass(frozen=True, slots=True)
class EconomicReceipt:
    publisher: EconomicPublisher
    source_url: str
    observed_at: datetime
    source_sha256: str
    byte_count: int
    archive_version: int = ARCHIVE_VERSION

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is not UTC:
            raise ValueError("economic archive observation time must be UTC")
        if not 0 < self.byte_count <= MAX_ECONOMIC_ICS_BYTES:
            raise ValueError("invalid economic archive size")
        if self.archive_version != ARCHIVE_VERSION:
            raise ValueError("unsupported economic capture archive version")

    @property
    def capture_id(self) -> str:
        return sha256(canonical_json(self._definition()).encode()).hexdigest()

    def _definition(self) -> dict[str, object]:
        return {
            "publisher": self.publisher,
            "source_url": self.source_url,
            "observed_at": self.observed_at.isoformat(),
            "source_sha256": self.source_sha256,
            "byte_count": self.byte_count,
            "archive_version": self.archive_version,
        }

    def as_dict(self) -> dict[str, object]:
        return {"capture_id": self.capture_id, **self._definition()}


def _now() -> datetime:
    return datetime.now(UTC)


def capture_official_ics(
    store: DatasetStore,
    publisher: EconomicPublisher,
    *,
    clock: Callable[[], datetime] = _now,
) -> EconomicReceipt:
    request = Request(
        publisher.url,
        headers={"Accept": "text/calendar,text/plain;q=0.9", "Accept-Encoding": "identity"},
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=20) as response:
            length = response.headers.get("Content-Length")
            raw: bytes = response.read(MAX_ECONOMIC_ICS_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(
            f"official {publisher.value.upper()} calendar returned HTTP {exc.code}"
        ) from None
    except (OSError, HTTPException):
        raise ValueError(f"official {publisher.value.upper()} calendar is unavailable") from None
    observed = clock()
    if length is not None and (not length.isdecimal() or int(length) != len(raw)):
        raise ValueError("official economic calendar response size did not match Content-Length")
    parse_economic_ics(raw)

    receipt = EconomicReceipt(publisher, publisher.url, observed, sha256(raw).hexdigest(), len(raw))
    with store.connection(write=True) as db:
        prior = db.execute(
            "SELECT id FROM economic_captures WHERE publisher = ? AND observed_at = ?",
            (publisher, observed.isoformat()),
        ).fetchone()
        if prior is not None:
            if prior["id"] != receipt.capture_id:
                raise ValueError("conflicting economic captures at the same observation time")
            return receipt
        db.execute(
            "INSERT INTO source_blobs VALUES (?, ?) ON CONFLICT (sha256) DO NOTHING",
            (receipt.source_sha256, raw),
        )
        db.execute(
            "INSERT INTO economic_captures VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.capture_id,
                publisher,
                receipt.source_url,
                observed.isoformat(),
                receipt.source_sha256,
                receipt.byte_count,
                receipt.archive_version,
            ),
        )
    return receipt


def economic_receipts(store: DatasetStore, limit: int = 100) -> list[EconomicReceipt]:
    if not 1 <= limit <= _MAX_ARCHIVE_PAGE:
        raise ValueError(f"economic capture limit must be between 1 and {_MAX_ARCHIVE_PAGE}")
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 14:
            return []
        rows = db.execute(
            "SELECT * FROM economic_captures ORDER BY observed_at DESC, id LIMIT ?", (limit,)
        ).fetchall()
    return [_receipt(row) for row in rows]


def economic_receipt(store: DatasetStore, capture_id: str) -> EconomicReceipt:
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 14:
            row = db.execute(
                "SELECT * FROM economic_captures WHERE id = ?", (capture_id,)
            ).fetchone()
            if row is not None:
                return _receipt(row)
    raise ValueError(f"unknown economic capture: {capture_id}")


def _receipt(row: sqlite3.Row) -> EconomicReceipt:
    receipt = EconomicReceipt(
        EconomicPublisher(row["publisher"]),
        row["source_url"],
        datetime.fromisoformat(row["observed_at"]),
        row["source_sha256"],
        row["byte_count"],
        row["archive_version"],
    )
    if receipt.capture_id != row["id"]:
        raise ValueError("economic capture metadata does not match its identity")
    return receipt


def economic_capture_bytes(store: DatasetStore, receipt: EconomicReceipt) -> bytes:
    with store.connection() as db:
        row = db.execute(
            "SELECT raw FROM source_blobs WHERE sha256 = ? AND length(raw) = ?",
            (receipt.source_sha256, receipt.byte_count),
        ).fetchone()
    if row is not None:
        raw = row["raw"]
        if isinstance(raw, bytes) and sha256(raw).hexdigest() == receipt.source_sha256:
            return raw
    raise ValueError("economic capture source bytes do not match their receipt")


def normalize_archived_ics(
    store: DatasetStore,
    capture_ids: tuple[str, ...],
    *,
    coverage: TradingWindow,
    title_prefixes: tuple[str, ...],
) -> EconomicCalendar:
    if not 1 <= len(capture_ids) <= MAX_ECONOMIC_CAPTURES:
        raise ValueError(f"provide between 1 and {MAX_ECONOMIC_CAPTURES} archived captures")
    receipts = tuple(economic_receipt(store, capture_id) for capture_id in capture_ids)
    publisher = receipts[0].publisher
    if any(receipt.publisher is not publisher for receipt in receipts):
        raise ValueError("archived captures must come from one official publisher")
    captures = tuple(
        EconomicIcsInput(
            receipt.observed_at,
            partial(economic_capture_bytes, store, receipt),
            receipt.capture_id,
        )
        for receipt in receipts
    )
    if len(captures) == 1:
        return normalize_economic_ics_input(
            captures[0],
            source=publisher.source,
            coverage=coverage,
            data_kind=EconomicSourceKind.OBSERVED_PUBLICATION,
            title_prefixes=title_prefixes,
        )
    return compare_economic_ics_inputs(
        captures,
        source=publisher.source,
        coverage=coverage,
        data_kind=EconomicSourceKind.OBSERVED_PUBLICATION,
        title_prefixes=title_prefixes,
    )
