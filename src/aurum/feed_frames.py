import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self

from aurum.instruments import MgcContract
from aurum.market_data import Candle, canonical_json
from aurum.providers import FeedStatus
from aurum.timestamps import minute_timestamp


@dataclass(frozen=True, slots=True)
class FeedFrame:
    candle: Candle
    source_status: FeedStatus
    received_at: datetime
    raw: bytes

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is not UTC or self.candle.available_at > self.received_at:
            raise ValueError("feed frame requires a closed minute and a UTC receipt time")
        if self.source_status is FeedStatus.UNAVAILABLE:
            raise ValueError("unavailable feeds cannot supply minute data")

    @classmethod
    def from_message(cls, raw: str | bytes, mode: FeedStatus, received_at: datetime) -> Self:
        payload = json.loads(raw)
        required = {
            "event",
            "contract",
            "opened_at",
            "open_ticks",
            "high_ticks",
            "low_ticks",
            "close_ticks",
            "volume",
        }
        if (
            not isinstance(payload, dict)
            or not required <= payload.keys() <= required | {"source_status"}
            or payload["event"] != "candle_closed"
        ):
            raise ValueError("invalid normalized candle-close frame")
        declared = FeedStatus(payload.get("source_status", mode))
        if declared is not mode:
            raise ValueError("source status disagrees with the configured feed")
        return cls(
            candle_from_dict(payload),
            declared,
            received_at,
            raw.encode() if isinstance(raw, str) else raw,
        )

    @classmethod
    def from_replay(cls, candle: Candle, mode: FeedStatus, received_at: datetime) -> Self:
        return cls(candle, mode, received_at, canonical_json(candle.as_dict()).encode())


def candle_from_dict(payload: dict[str, object]) -> Candle:
    contract, opened_at = payload["contract"], payload["opened_at"]
    if not isinstance(contract, str) or not isinstance(opened_at, str):
        raise ValueError("feed contract and candle open must be text")
    numbers = [
        _integer(payload[name])
        for name in ("open_ticks", "high_ticks", "low_ticks", "close_ticks", "volume")
    ]
    return Candle(MgcContract(contract), minute_timestamp(opened_at), *numbers)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("feed ticks and volume must be integers")
    return value
