from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from io import BytesIO

_UNITS_PER_MINUTE = {"s": 60, "ms": 60_000, "us": 60_000_000, "ns": 60_000_000_000}


def parquet_rows(raw: bytes, columns: tuple[str, ...]) -> Iterator[tuple[int, list[str]]]:
    try:
        arrow = import_module("pyarrow")
        parquet = import_module("pyarrow.parquet")
    except ModuleNotFoundError as exc:
        raise ValueError("Parquet support requires the aurum-research[parquet] extra") from exc

    try:
        with BytesIO(raw) as stream:
            source = parquet.ParquetFile(stream)
            names = source.schema_arrow.names
            if len(names) != len(columns) or set(names) != set(columns):
                raise ValueError(f"Parquet columns must be {','.join(columns)}")
            stamp_type = source.schema_arrow.field("timestamp").type
            timestamp_is_native = arrow.types.is_timestamp(stamp_type)
            line = 0
            for batch in source.iter_batches(columns=list(columns), batch_size=16_384):
                if timestamp_is_native:
                    stamps = batch.column(batch.schema.get_field_index("timestamp"))
                    unit = _UNITS_PER_MINUTE[stamp_type.unit]
                    for index, value in enumerate(stamps.cast(arrow.int64()).to_pylist(), start=1):
                        if value is not None and value % unit:
                            raise ValueError(
                                f"Parquet row {line + index}: timestamp must be on a whole minute"
                            )
                values = batch.to_pydict()
                for index in range(batch.num_rows):
                    line += 1
                    try:
                        yield (
                            line,
                            [_field_text(column, values[column][index]) for column in columns],
                        )
                    except ValueError as exc:
                        raise ValueError(f"Parquet row {line}: {exc}") from exc
    except arrow.ArrowException as exc:
        raise ValueError(f"invalid Parquet data: {exc}") from exc


def _field_text(column: str, value: object) -> str:
    if isinstance(value, str):
        return value
    if column == "timestamp" and isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, bool):
        if column == "volume" and isinstance(value, int):
            return str(value)
        if column in ("open", "high", "low", "close") and isinstance(value, int | float | Decimal):
            return str(value)
    raise ValueError(f"invalid value for column {column}")
