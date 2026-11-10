"""Rebuild normalized immutable dataset rows from their preserved original bytes."""

from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib.metadata import version
from itertools import zip_longest

from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import (
    IMPORT_VERSION,
    TRADE_IMPORT_VERSION,
    DataKind,
    ImportConfig,
    TimestampLabel,
    canonical_json,
    prepare_history,
    prepare_trade_history,
)
from aurum.providers import SourceFormat
from aurum.storage import DatasetStore

DATASET_AUDIT_VERSION = 1


@dataclass(frozen=True, slots=True)
class _PreservedSource:
    format: SourceFormat
    origin: str | None
    raw: bytes

    def read(self) -> bytes:
        return self.raw


def verify_dataset(store: DatasetStore, dataset_id: str) -> dict[str, object]:
    metadata = store.describe(dataset_id)
    provenance = metadata["provenance"]
    if provenance.get("dataset_version") != IMPORT_VERSION:
        raise ValueError("dataset verification requires the original import version")
    if provenance.get("tzdata_version") != version("tzdata"):
        raise ValueError("dataset verification requires the original tzdata version")
    trades = provenance.get("source_data_type") == "trade_prints"
    if trades and provenance.get("trade_import_version") != TRADE_IMPORT_VERSION:
        raise ValueError("dataset verification requires the original trade import version")
    raw = store.source_bytes(dataset_id)
    checksum = sha256(raw).hexdigest()
    if checksum != provenance.get("source_sha256"):
        raise ValueError("preserved dataset source checksum mismatch")
    origin = provenance.get("source_origin")
    if origin is not None and not isinstance(origin, str):
        raise ValueError("stored source origin is invalid")
    source = _PreservedSource(SourceFormat(str(provenance["format"])), origin, raw)
    config = ImportConfig(
        str(provenance["provider"]),
        MgcContract(str(provenance["contract"])),
        str(provenance["source_timezone"]),
        TimestampLabel(str(provenance["timestamp_label"])),
        DataKind(str(provenance["data_kind"])),
    )
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    prepare = prepare_trade_history if trades else prepare_history
    rebuilt = prepare(source, config, calendar)
    if rebuilt.dataset_id != dataset_id or rebuilt.provenance_json != canonical_json(provenance):
        raise ValueError("rebuilt dataset provenance or identity differs")
    span = TradingWindow(rebuilt.candles[0].opened_at, rebuilt.candles[-1].available_at)
    replayable = all(issue.code != "missing_candles" for issue in rebuilt.issues)
    if (
        metadata["first_open"] != span.start.isoformat()
        or metadata["last_close"] != span.end.isoformat()
        or metadata["candle_count"] != len(rebuilt.candles)
        or metadata["issues"] != [asdict(issue) for issue in rebuilt.issues]
        or metadata["replayable"] != replayable
    ):
        raise ValueError("stored dataset bounds, quality findings or metadata differ")

    with store.connection() as db:
        row = db.execute(
            """SELECT source_sha256,
                      (SELECT count(*) FROM candles WHERE dataset_id = datasets.id) AS candles
               FROM datasets WHERE id = ?""",
            (dataset_id,),
        ).fetchone()
        if (
            row is None
            or row["source_sha256"] != checksum
            or row["candles"] != len(rebuilt.candles)
        ):
            raise ValueError("stored candle count or source reference differs")
        trade_count = 0
        if db.execute("PRAGMA user_version").fetchone()[0] >= 12:
            trade_count = db.execute(
                "SELECT count(*) FROM trade_prints WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()[0]
        if trade_count != len(rebuilt.trade_prints):
            raise ValueError("stored trade print count differs")

    candles = (candle for batch in store.inspection_batches(dataset_id, span) for candle in batch)
    for actual, expected in zip_longest(candles, rebuilt.candles):
        if actual != expected:
            candle = expected if expected is not None else actual
            assert candle is not None
            raise ValueError(f"stored normalized candle differs at {candle.opened_at.isoformat()}")
    if trades:
        for actual_trade, expected_trade in zip_longest(
            store.trade_prints(dataset_id), rebuilt.trade_prints
        ):
            if actual_trade != expected_trade:
                trade = expected_trade if expected_trade is not None else actual_trade
                assert trade is not None
                raise ValueError(
                    f"stored normalized trade print differs at sequence {trade.sequence}"
                )
    return {
        "dataset_audit_version": DATASET_AUDIT_VERSION,
        "dataset_id": dataset_id,
        "data_kind": config.data_kind,
        "contract": config.contract.code,
        "source_sha256": checksum,
        "source_format": source.format,
        "candle_count": len(rebuilt.candles),
        "trade_print_count": len(rebuilt.trade_prints),
        "replayable": replayable,
        "verified": True,
    }
