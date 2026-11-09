import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import import_module
from time import monotonic, sleep
from typing import Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.feed_frames import FeedFrame
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, prepare_history
from aurum.monitoring import MarketMonitor, MonitorState, describe_monitor
from aurum.providers import FeedStatus, HttpsHistorySource, SourceFormat


class FrameConnection(Protocol):
    def recv(self, timeout: float | None = None) -> str | bytes: ...


class FeedDisconnected(Exception):
    """The authorized transport ended without an integrity fault."""


@dataclass(frozen=True, slots=True)
class WebSocketSource:
    url: str
    mode: FeedStatus
    token_env: str | None = None

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        local_fixture = (
            self.mode is FeedStatus.SIMULATED
            and parsed.scheme == "ws"
            and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        )
        if (
            (parsed.scheme != "wss" and not local_fixture)
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("stream endpoint must be WSS without embedded credentials")
        if self.mode not in (FeedStatus.REAL_TIME, FeedStatus.DELAYED, FeedStatus.SIMULATED):
            raise ValueError("stream mode must be real_time, delayed or simulated")
        if local_fixture and self.token_env is not None:
            raise ValueError("plaintext synthetic loopback cannot carry credentials")

    @property
    def origin(self) -> str:
        endpoint = urlsplit(self.url)
        return f"{endpoint.scheme}://{endpoint.netloc}"

    @property
    def fingerprint(self) -> str:
        return sha256(self.url.encode()).hexdigest()

    @contextmanager
    def connect(self) -> Iterator[FrameConnection]:
        try:
            client = import_module("websockets.sync.client")
            errors = import_module("websockets.exceptions")
        except ModuleNotFoundError as exc:
            raise ValueError("WebSocket support requires the aurum-research[stream] extra") from exc
        headers = {}
        if self.token_env is not None:
            token = os.environ.get(self.token_env)
            if not token:
                raise ValueError(f"stream access variable {self.token_env} is unset")
            headers["Authorization"] = f"Bearer {token}"
        try:
            with client.connect(
                self.url,
                additional_headers=headers,
                proxy=None,
                open_timeout=10,
                close_timeout=5,
                max_size=64 * 1024,
            ) as connection:
                yield connection
        except (errors.WebSocketException, OSError) as exc:
            raise FeedDisconnected(type(exc).__name__) from None


@dataclass(frozen=True, slots=True)
class HttpsGapBackfill:
    url: str
    token_env: str | None = None

    def __post_init__(self) -> None:
        HttpsHistorySource(self.url, SourceFormat.CSV, self.token_env)

    def recover(
        self, monitor: MarketMonitor, gap: TradingWindow, clock: Callable[[], datetime]
    ) -> None:
        if monitor.config.source_status not in (FeedStatus.REAL_TIME, FeedStatus.DELAYED):
            raise ValueError("historical backfill requires a real-time or delayed feed")
        end = min(gap.end, gap.start + timedelta(days=7))
        span = TradingWindow(gap.start, end)
        original = monitor.calendar
        sessions = tuple(
            session
            for session in original.sessions or ()
            if session.start < span.end and session.end > span.start
        )
        calendar = SessionCalendar(original.source, original.version, span, sessions=sessions)
        request = urlsplit(self.url)
        query = urlencode(
            {
                "contract": monitor.contract.code,
                "start": span.start.isoformat(),
                "end": span.end.isoformat(),
            }
        )
        if request.query:
            query = f"{request.query}&{query}"
        source = HttpsHistorySource(
            urlunsplit(request._replace(query=query)), SourceFormat.CSV, self.token_env
        )
        config = ImportConfig(
            f"{monitor.config.source} backfill",
            monitor.contract,
            "UTC",
            TimestampLabel.OPEN,
            DataKind.HISTORICAL,
        )
        dataset_id = monitor.store.save(prepare_history(source, config, calendar))
        received_at = clock()
        for batch in monitor.store.replay_batches(dataset_id, span):
            for candle in batch:
                monitor.ingest(FeedFrame.from_replay(candle, FeedStatus.HISTORICAL, received_at))
        monitor.note(received_at, "feed_backfill_completed", dataset_id)


def run_stream(
    monitor: MarketMonitor,
    source: WebSocketSource,
    *,
    backfill: HttpsGapBackfill | None = None,
    max_frames: int | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleeper: Callable[[float], None] = sleep,
) -> dict[str, object]:
    if source.mode is not monitor.config.source_status:
        raise ValueError("transport and monitor classifications differ")
    if max_frames is not None and max_frames < 1:
        raise ValueError("frame limit must be positive")
    if backfill is not None and source.mode is FeedStatus.SIMULATED:
        raise ValueError("simulated streams require simulated gap frames")
    handled = 0
    retry_delay = 1
    last_backfill: float | None = None

    def try_backfill() -> None:
        nonlocal last_backfill
        if backfill is None or (gap := monitor.gap()) is None:
            return
        timer = monotonic()
        if last_backfill is not None and timer - last_backfill < 15:
            return
        last_backfill = timer
        try:
            backfill.recover(monitor, gap, clock)
        except (OSError, ValueError):
            monitor.note(
                clock(), "feed_backfill_failed", "authorized backfill unavailable or incomplete"
            )

    try:
        while max_frames is None or handled < max_frames:
            try:
                with source.connect() as connection:
                    monitor.set_connection(True, clock())
                    while max_frames is None or handled < max_frames:
                        try:
                            raw = connection.recv(timeout=1)
                        except TimeoutError:
                            monitor.refresh(clock())
                            try_backfill()
                            continue
                        now = clock()
                        try:
                            frame = FeedFrame.from_message(raw, source.mode, now)
                            status = monitor.ingest(frame)
                        except (ValueError, KeyError, TypeError, OverflowError):
                            monitor.fault(now, "invalid_normalized_frame")
                            return monitor.set_connection(False, now)
                        handled += 1
                        retry_delay = 1
                        if status["state"] == MonitorState.INVALID:
                            return monitor.set_connection(False, now)
                        try_backfill()
            except FeedDisconnected as exc:
                now = clock()
                monitor.set_connection(False, now)
                monitor.note(now, "transport_retry_scheduled", str(exc))
                sleeper(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.set_connection(False, clock())
    return describe_monitor(monitor.store, monitor.monitor_id)
