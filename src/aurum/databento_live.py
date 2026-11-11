"""Explicitly launched native Databento minute feed with preserved DBN evidence."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import import_module
from queue import Empty, Full, Queue
from re import fullmatch
from threading import Event
from time import monotonic
from typing import Any

from aurum.feed_frames import FeedFrame
from aurum.instruments import MgcContract
from aurum.market_data import Candle, canonical_json
from aurum.monitoring import MarketMonitor, describe_monitor
from aurum.providers import FeedStatus
from aurum.streaming import FeedDisconnected, FrameConnection

DATABENTO_LIVE_VERSION = 1
SDK_VERSION = "0.90.0"
DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_UNDEFINED = 2**64 - 1


def _transient_subscription_error(error: Exception, sdk: Any) -> bool:
    """The pinned SDK wraps socket/auth-timeout failures in BentoError.

    Inspect exception types and causal context, never provider error text. An
    unqualified SDK authorization rejection remains a permanent failure.
    """
    current: BaseException | None = error
    for _ in range(8):
        if isinstance(current, OSError):
            return True
        if not isinstance(current, sdk.BentoError):
            return False
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return False


def bind_native_resume(source: "DatabentoSource", monitor: MarketMonitor) -> "DatabentoSource":
    def resume() -> datetime:
        expected = describe_monitor(monitor.store, monitor.monitor_id)["next_expected_open"]
        if not isinstance(expected, str):
            raise ValueError("Databento monitoring requires additional reviewed schedule coverage")
        next_open = datetime.fromisoformat(expected)
        if source.start is not None and source.start > next_open:
            raise ValueError("Databento replay start cannot skip an expected monitor minute")
        return next_open

    return replace(source, resume_from=resume)


@dataclass(frozen=True, slots=True)
class DatabentoSource:
    contract: MgcContract
    raw_symbol: str
    key_env: str = "DATABENTO_API_KEY"
    start: datetime | None = None
    queue_capacity: int = 4096
    clock: Callable[[], datetime] = field(
        default=lambda: datetime.now(UTC), repr=False, compare=False
    )
    resume_from: Callable[[], datetime | None] | None = field(
        default=None, repr=False, compare=False
    )
    mode: FeedStatus = field(default=FeedStatus.REAL_TIME, init=False)

    def __post_init__(self) -> None:
        _, year, month = self.contract.code.split("-")
        match = fullmatch(r"MGC([FGHJKMNQUVXZ])([0-9]{1,4})", self.raw_symbol)
        if (
            match is None
            or match[1] != "FGHJKMNQUVXZ"[int(month) - 1]
            or not year.endswith(match[2])
        ):
            raise ValueError("Databento raw symbol disagrees with the dated MGC contract")
        if not fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.key_env):
            raise ValueError("Databento key must reference an environment variable name")
        if type(self.queue_capacity) is not int or not 1 <= self.queue_capacity <= 65_536:
            raise ValueError("Databento queue capacity must be between 1 and 65536")
        if self.start is not None and (
            self.start.tzinfo is not UTC or self.start.second or self.start.microsecond
        ):
            raise ValueError("Databento replay start must be a whole UTC minute")

    @property
    def origin(self) -> str:
        return f"databento://{DATASET}"

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self.definition()).encode()).hexdigest()

    def definition(self) -> dict[str, object]:
        return {
            "adapter_version": DATABENTO_LIVE_VERSION,
            "sdk_version": SDK_VERSION,
            "dataset": DATASET,
            "schema": SCHEMA,
            "contract": self.contract.code,
            "raw_symbol": self.raw_symbol,
            "start": self.start.isoformat() if self.start else None,
            "queue_capacity": self.queue_capacity,
        }

    @contextmanager
    def connect(self) -> Iterator[FrameConnection]:
        key = os.environ.get(self.key_env)
        if not key:
            raise ValueError(f"Databento access variable {self.key_env} is unset")
        requested = self.resume_from() if self.resume_from is not None else None
        requested = requested if requested is not None else self.start
        now = self.clock()
        if requested is not None and (
            requested.tzinfo is not UTC or requested.second or requested.microsecond
        ):
            raise ValueError("Databento resume cursor must be a whole UTC minute")
        if requested is not None and now - requested >= timedelta(hours=23):
            raise ValueError(
                "Databento intraday replay start must be within the preceding 23 hours"
            )
        if self.start is not None and self.start > now:
            raise ValueError("Databento replay cannot start in the future")
        if requested is not None and requested > now and self.resume_from is None:
            raise ValueError("Databento replay cannot start in the future")
        replay_start = (
            requested if requested is not None and now - requested >= timedelta(minutes=1) else None
        )
        try:
            sdk = import_module("databento")
        except ModuleNotFoundError as exc:
            raise ValueError("Databento feed requires the aurum-research[databento] extra") from exc
        if sdk.__version__ != SDK_VERSION:
            raise ValueError(f"Databento feed requires the verified SDK version {SDK_VERSION}")
        try:
            client = sdk.Live(
                key=key,
                reconnect_policy="none",
                slow_reader_behavior="warn",
                heartbeat_interval_s=5,
            )
        except Exception:
            raise ValueError("Databento client initialization failed") from None
        connection = DatabentoConnection(self, client, sdk, replay_start=replay_start)
        try:
            try:
                client.add_callback(connection.on_record, connection.on_error)
                client.subscribe(
                    dataset=DATASET,
                    schema=SCHEMA,
                    stype_in="raw_symbol",
                    symbols=[self.raw_symbol],
                    start=replay_start,
                )
                client.start()
            except Exception as exc:
                if _transient_subscription_error(exc, sdk):
                    raise FeedDisconnected("databento_transport_unavailable") from None
                raise ValueError(
                    "Databento subscription failed; verify authorized access"
                ) from None
            yield connection
        finally:
            # SDK error bodies may contain credentials. Never persist them or
            # replace an existing integrity failure with a cleanup error.
            with suppress(Exception):
                client.terminate()
                client.block_for_close(timeout=5)


class DatabentoConnection:
    def __init__(
        self,
        source: DatabentoSource,
        client: Any,
        sdk: Any,
        *,
        replay_start: datetime | None = None,
    ) -> None:
        self.source, self.client, self.sdk = source, client, sdk
        self.queue: Queue[tuple[Any, datetime]] = Queue(maxsize=source.queue_capacity)
        self.failed = Event()
        self.mappings: dict[int, Any] = {}
        self.publisher: int | None = None
        self.replay_start = replay_start
        self.replaying = replay_start is not None

    def on_record(self, record: Any) -> None:
        try:
            self.queue.put_nowait((record, self.source.clock()))
        except Full:
            self.failed.set()

    def on_error(self, _: Exception) -> None:
        self.failed.set()

    def recv(self, timeout: float | None = None) -> FeedFrame:
        deadline = monotonic() + (timeout if timeout is not None else 1)
        while True:
            if self.failed.is_set():
                raise ValueError("Databento callback queue integrity failed")
            if not self.client.is_connected():
                raise FeedDisconnected("databento_session_ended")
            try:
                record, received = self.queue.get(timeout=max(0, deadline - monotonic()))
            except Empty:
                if not self.client.is_connected():
                    raise FeedDisconnected("databento_session_ended") from None
                raise TimeoutError from None
            frame = self.decode(record, received)
            if self.failed.is_set():
                raise ValueError("Databento callback queue integrity failed")
            if frame is not None:
                if not self.client.is_connected():
                    raise FeedDisconnected("databento_session_ended")
                return frame
            if monotonic() >= deadline:
                raise TimeoutError

    def decode(self, record: Any, received: datetime) -> FeedFrame | None:
        if isinstance(record, self.sdk.ErrorMsg):
            raise ValueError("Databento gateway reported a session error")
        if isinstance(record, self.sdk.SystemMsg):
            code = int(record.code)
            if code == 2:
                raise ValueError("Databento gateway reported a slow reader")
            if code == 3:
                self.replaying = False
            elif code not in (0, 1, 4):
                raise ValueError("unsupported Databento system message")
            return None
        if isinstance(record, self.sdk.SymbolMappingMsg):
            if (
                record.stype_in_symbol != self.source.raw_symbol
                or record.stype_out_symbol != self.source.raw_symbol
                or record.stype_in != self.sdk.SType.RAW_SYMBOL
                or record.stype_out != self.sdk.SType.RAW_SYMBOL
                or len(self.mappings) >= 16
                and record.instrument_id not in self.mappings
            ):
                raise ValueError("Databento symbol mapping disagrees with the pinned contract")
            self.mappings[record.instrument_id] = record
            return None
        if not isinstance(record, self.sdk.OHLCVMsg) or int(record.rtype) != 33:
            raise ValueError("Databento feed requires native ohlcv-1m records")
        if record.instrument_id <= 0 or record.publisher_id <= 0:
            raise ValueError("Databento minute requires a publisher and instrument identity")
        mapping = self.mappings.get(record.instrument_id)
        if mapping is None:
            raise ValueError("Databento minute has no preceding dated symbol mapping")
        if (
            mapping.start_ts != _UNDEFINED
            and record.ts_event < mapping.start_ts
            or mapping.end_ts != _UNDEFINED
            and record.ts_event >= mapping.end_ts
        ):
            raise ValueError("Databento minute lies outside its symbol mapping interval")
        if self.publisher is not None and record.publisher_id != self.publisher:
            raise ValueError("Databento publisher changed within a pinned feed")
        self.publisher = record.publisher_id
        if record.ts_event % 60_000_000_000:
            raise ValueError("Databento minute does not start on a whole UTC minute")
        prices = (record.open, record.high, record.low, record.close)
        if any(price <= 0 or price % 100_000_000 for price in prices):
            raise ValueError("Databento prices must be positive exact MGC ticks")
        candle = Candle(
            self.source.contract,
            _EPOCH + timedelta(microseconds=record.ts_event // 1000),
            record.open // 100_000_000,
            record.high // 100_000_000,
            record.low // 100_000_000,
            record.close // 100_000_000,
            record.volume,
        )
        raw = canonical_json(
            {
                "adapter": self.source.definition(),
                "subscription_replay_start": self.replay_start.isoformat()
                if self.replay_start
                else None,
                "mapping_dbn_hex": bytes(mapping).hex(),
                "ohlcv_dbn_hex": bytes(record).hex(),
            }
        ).encode()
        status = FeedStatus.HISTORICAL if self.replaying else self.source.mode
        return FeedFrame(candle, status, received, raw)


def frame_from_preserved_native(
    payload: dict[str, Any],
    status: FeedStatus,
    received_at: datetime,
) -> tuple[FeedFrame, str]:
    """Rebuild serialized SDK records without creating a client or reading a key."""
    try:
        adapter = payload["adapter"]
        source = DatabentoSource(
            MgcContract(adapter["contract"]),
            adapter["raw_symbol"],
            start=datetime.fromisoformat(adapter["start"]) if adapter["start"] else None,
            queue_capacity=adapter["queue_capacity"],
        )
        if adapter != source.definition() or payload.keys() != {
            "adapter",
            "mapping_dbn_hex",
            "ohlcv_dbn_hex",
            "subscription_replay_start",
        }:
            raise ValueError("native frame requires its original adapter definition")
        sdk = import_module("databento")
        dbn = import_module("databento_dbn")
        if sdk.__version__ != SDK_VERSION:
            raise ValueError("native audit requires its original SDK version")
        decoder = dbn.DBNDecoder(has_metadata=False)
        records = []
        for key in ("mapping_dbn_hex", "ohlcv_dbn_hex"):
            raw = bytes.fromhex(payload[key])
            try:
                decoder.write(raw)
                decoded = decoder.decode()
            except Exception:
                raise ValueError("preserved native record bytes could not be decoded") from None
            if len(decoded) != 1 or decoder.buffer() or bytes(decoded[0]) != raw:
                raise ValueError("preserved native record bytes are incomplete or inconsistent")
            records.append(decoded[0])
        if not isinstance(records[0], sdk.SymbolMappingMsg) or not isinstance(
            records[1], sdk.OHLCVMsg
        ):
            raise ValueError("preserved native records have unexpected types")
        connection = DatabentoConnection(source, None, sdk)
        connection.decode(records[0], received_at)
        frame = connection.decode(records[1], received_at)
        if frame is None:
            raise ValueError("preserved native minute could not be reconstructed")
        replay_start = payload["subscription_replay_start"]
        if replay_start is not None:
            started = datetime.fromisoformat(replay_start)
            if started.tzinfo is not UTC or started > frame.candle.opened_at:
                raise ValueError("native replay bounds do not contain the recorded minute")
        return FeedFrame(
            frame.candle, status, received_at, canonical_json(payload).encode()
        ), source.fingerprint
    except ModuleNotFoundError:
        raise ValueError("native frame audit requires the pinned Databento extra") from None
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("preserved native frame evidence is invalid or unsupported") from None
