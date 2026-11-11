"""Decode explicitly pretty-formatted Databento minute-bar CSV exports."""

from collections.abc import Iterator
from re import fullmatch

from aurum.instruments import MgcContract
from aurum.timestamps import event_timestamp

DATABENTO_CSV_VERSION = 1
DATABENTO_OHLCV_COLUMNS = (
    "ts_event",
    "rtype",
    "publisher_id",
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "symbol",
)
_MONTHS = "FGHJKMNQUVXZ"


def normalized_ohlcv_rows(
    rows: Iterator[tuple[int, list[str]]], contract: MgcContract
) -> Iterator[tuple[int, list[str]]]:
    instrument: tuple[str, str] | None = None
    _, year, month = contract.code.split("-")
    for line, row in rows:
        try:
            if len(row) != len(DATABENTO_OHLCV_COLUMNS):
                raise ValueError("wrong number of Databento CSV fields")
            stamp, rtype, publisher, identifier, open_, high, low, close, volume, symbol = row
            if rtype != "33":
                raise ValueError("Databento import requires ohlcv-1m records with rtype 33")
            match = fullmatch(r"MGC([FGHJKMNQUVXZ])([0-9]{1,4})", symbol)
            if match is None or match[1] != _MONTHS[int(month) - 1] or not year.endswith(match[2]):
                raise ValueError("Databento raw symbol disagrees with the dated MGC contract")
            if not publisher.isdecimal() or not 0 < int(publisher) <= 65535:
                raise ValueError("invalid Databento publisher ID")
            if not identifier.isdecimal() or not 0 < int(identifier) < 2**32:
                raise ValueError("invalid Databento instrument ID")
            current = publisher, identifier
            if instrument is not None and current != instrument:
                raise ValueError("Databento import requires one publisher and instrument ID")
            instrument = current
            if not stamp.endswith(("Z", "+00:00")):
                raise ValueError("export with pretty_ts=True and UTC timestamps")
            when = event_timestamp(stamp)
            if when.nanosecond_tail or when.time.second or when.time.microsecond:
                raise ValueError("Databento minute interval must open on a whole UTC minute")
            if any(
                fullmatch(r"[0-9]+\.[0-9]{9}", price) is None for price in (open_, high, low, close)
            ):
                raise ValueError("export with pretty_px=True and nine decimal places")
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Databento CSV line {line}: {exc}") from exc
        yield line, [when.time.isoformat(), contract.code, open_, high, low, close, volume]
