import os
from dataclasses import dataclass
from enum import StrEnum
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SourceFormat(StrEnum):
    CSV = "csv"
    PARQUET = "parquet"
    DATABENTO_OHLCV_CSV = "databento_ohlcv_csv"


class FeedStatus(StrEnum):
    REAL_TIME = "real_time"
    DELAYED = "delayed"
    HISTORICAL = "historical"
    SIMULATED = "simulated"
    UNAVAILABLE = "unavailable"


class HistoricalSource(Protocol):
    @property
    def format(self) -> SourceFormat: ...

    @property
    def origin(self) -> str | None: ...

    def read(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class FileHistorySource:
    path: Path
    format: SourceFormat

    @property
    def origin(self) -> str | None:
        return None

    def read(self) -> bytes:
        return self.path.read_bytes()


@dataclass(frozen=True, slots=True)
class HttpsHistorySource:
    url: str
    format: SourceFormat
    token_env: str | None = None
    timeout_seconds: int = 30
    max_bytes: int = 256 * 1024**2

    def __post_init__(self) -> None:
        url = urlsplit(self.url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.fragment
        ):
            raise ValueError("historical endpoint must be HTTPS without embedded credentials")
        if min(self.timeout_seconds, self.max_bytes) < 1:
            raise ValueError("historical endpoint timeout and byte limit must be positive")

    @property
    def origin(self) -> str:
        return f"https://{urlsplit(self.url).netloc}"

    def read(self) -> bytes:
        headers = {
            "Accept": "application/octet-stream"
            if self.format is SourceFormat.PARQUET
            else "text/csv"
        }
        if self.token_env is not None:
            token = os.environ.get(self.token_env)
            if not token:
                raise ValueError(f"historical access variable {self.token_env} is unset")
            headers["Authorization"] = f"Bearer {token}"
        try:
            request = Request(self.url, headers=headers)
            with build_opener(NoRedirect).open(request, timeout=self.timeout_seconds) as response:
                raw: bytes = response.read(self.max_bytes + 1)
        except HTTPError as exc:
            raise ValueError(
                f"historical request to {self.origin} failed with HTTP {exc.code}"
            ) from None
        except (OSError, ValueError):
            raise ValueError(f"historical request to {self.origin} failed") from None
        if len(raw) > self.max_bytes:
            raise ValueError("historical response exceeded configured byte limit")
        return raw


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> None:
        return None
