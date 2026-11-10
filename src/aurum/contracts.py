import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from aurum.instruments import MgcContract
from aurum.market_data import DataKind
from aurum.timestamps import minute_timestamp

CONTRACT_VERSION = 1
_BPS = 10_000


@dataclass(frozen=True, slots=True)
class ContractListing:
    contract: MgcContract
    first_trade_at: datetime
    last_trade_at: datetime
    known_at: datetime

    def __post_init__(self) -> None:
        if (
            any(
                stamp.tzinfo is not UTC
                for stamp in (self.first_trade_at, self.last_trade_at, self.known_at)
            )
            or self.first_trade_at >= self.last_trade_at
        ):
            raise ValueError("contract requires an ordered UTC listing window")

    def as_dict(self) -> dict[str, str]:
        return {
            "contract": self.contract.code,
            "first_trade_at": self.first_trade_at.isoformat(),
            "last_trade_at": self.last_trade_at.isoformat(),
            "known_at": self.known_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ContractActivity:
    contract: MgcContract
    period_start: datetime
    period_end: datetime
    available_at: datetime
    volume: int | None = None
    open_interest: int | None = None

    def __post_init__(self) -> None:
        if (
            any(
                stamp.tzinfo is not UTC
                for stamp in (self.period_start, self.period_end, self.available_at)
            )
            or not self.period_start < self.period_end <= self.available_at
        ):
            raise ValueError("contract activity must have an available, completed UTC period")
        measures = (self.volume, self.open_interest)
        if all(value is None for value in measures) or any(
            value is not None and value < 0 for value in measures
        ):
            raise ValueError("contract activity needs a nonnegative volume or open interest")

    @property
    def period(self) -> tuple[datetime, datetime]:
        return self.period_start, self.period_end

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract.code,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "available_at": self.available_at.isoformat(),
            "volume": self.volume,
            "open_interest": self.open_interest,
        }


class RollReason(StrEnum):
    NO_ELIGIBLE = "no_eligible_contract"
    FIRST = "initial_selection"
    KEEP = "keep_current"
    VOLUME = "volume_lead"
    OPEN_INTEREST = "open_interest_lead"
    EXPIRY = "expiry_window"
    EXPIRED = "current_expired"
    NO_SUCCESSOR = "no_eligible_successor"


@dataclass(frozen=True, slots=True)
class RollPolicy:
    expiry_lead_days: int = 3
    volume_lead_bps: int = _BPS
    open_interest_lead_bps: int | None = None

    def __post_init__(self) -> None:
        if self.expiry_lead_days < 0 or self.volume_lead_bps < _BPS:
            raise ValueError("roll expiry lead must be nonnegative and volume lead at least 100%")
        if self.open_interest_lead_bps is not None and self.open_interest_lead_bps < _BPS:
            raise ValueError("open-interest lead must be at least 100%")


DEFAULT_ROLL_POLICY = RollPolicy()


@dataclass(frozen=True, slots=True)
class RollDecision:
    as_of: datetime
    current: MgcContract | None
    selected: MgcContract | None
    reason: RollReason
    available: tuple[ContractListing, ...]
    evidence: tuple[ContractActivity, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "current": self.current.code if self.current else None,
            "selected": self.selected.code if self.selected else None,
            "reason": self.reason,
            "available": [listing.as_dict() for listing in self.available],
            "evidence": [observation.as_dict() for observation in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class ContractCatalog:
    source: str
    version: str
    data_kind: DataKind
    listings: tuple[ContractListing, ...]
    activity: tuple[ContractActivity, ...] = ()

    def __post_init__(self) -> None:
        contracts = {item.contract for item in self.listings}
        if (
            not self.source.strip()
            or not self.version.strip()
            or len(contracts) != len(self.listings)
        ):
            raise ValueError("catalog requires source, version and unique listings")
        identities = {(item.contract, item.period, item.available_at) for item in self.activity}
        if len(identities) != len(self.activity) or any(
            item.contract not in contracts for item in self.activity
        ):
            raise ValueError("activity must uniquely reference a listed contract")

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))

    @classmethod
    def from_dict(cls, payload: object) -> Self:
        if (
            not isinstance(payload, dict)
            or not {"source", "version", "data_kind", "listings"}
            <= payload.keys()
            <= {"source", "version", "data_kind", "listings", "activity"}
            or not isinstance(payload["source"], str)
            or not isinstance(payload["version"], str)
            or not isinstance(payload["listings"], list)
            or not isinstance(payload.get("activity", []), list)
        ):
            raise ValueError("invalid contract catalog document")
        return cls(
            payload["source"],
            payload["version"],
            DataKind(payload["data_kind"]),
            tuple(_listing(item) for item in payload["listings"]),
            tuple(_activity(item) for item in payload.get("activity", [])),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "version": self.version,
            "data_kind": self.data_kind,
            "listings": [item.as_dict() for item in self.listings],
            "activity": [item.as_dict() for item in self.activity],
        }

    def available_at(self, as_of: datetime) -> tuple[ContractListing, ...]:
        if as_of.tzinfo is not UTC:
            raise ValueError("contract decisions require UTC as-of timestamps")
        return tuple(
            sorted(
                (
                    item
                    for item in self.listings
                    if max(item.known_at, item.first_trade_at) <= as_of < item.last_trade_at
                ),
                key=lambda item: (item.last_trade_at, item.contract.code),
            )
        )

    def decide(
        self,
        as_of: datetime,
        current: MgcContract | None = None,
        policy: RollPolicy = DEFAULT_ROLL_POLICY,
    ) -> RollDecision:
        available = self.available_at(as_of)
        cohorts = self._activity_at(as_of)
        if current is None:
            selected, evidence = _rank_available(available, cohorts)
            return RollDecision(
                as_of,
                current,
                selected,
                RollReason.FIRST if selected else RollReason.NO_ELIGIBLE,
                available,
                evidence,
            )
        listing = next(
            (item for item in self.listings if item.contract == current and item.known_at <= as_of),
            None,
        )
        if listing is None or as_of < listing.first_trade_at:
            raise ValueError("current contract was not available at the decision time")

        later = tuple(item for item in available if item.last_trade_at > listing.last_trade_at)
        expiry = listing.last_trade_at - as_of
        if expiry <= timedelta(days=policy.expiry_lead_days):
            selected, evidence = _rank_available(later, cohorts)
            reason = RollReason.EXPIRED if expiry <= timedelta(0) else RollReason.EXPIRY
            if selected is None:
                reason = RollReason.NO_SUCCESSOR
                if expiry > timedelta(0):
                    selected = current
            return RollDecision(as_of, current, selected, reason, available, evidence)

        eligible: list[
            tuple[datetime, datetime, str, RollReason, ContractActivity, ContractActivity]
        ] = []
        for candidate in later:
            for cohort in cohorts:
                left = cohort.get(current)
                right = cohort.get(candidate.contract)
                if left is None or right is None:
                    continue
                lead = _lead(left, right, policy)
                if lead is not None:
                    eligible.append(
                        (
                            right.period_end,
                            candidate.last_trade_at,
                            candidate.contract.code,
                            lead,
                            left,
                            right,
                        )
                    )
                if _has_comparison(left, right, policy):
                    break
        if not eligible:
            return RollDecision(as_of, current, current, RollReason.KEEP, available)
        _, _, _, reason, left, right = min(
            eligible, key=lambda item: (-item[0].timestamp(), item[1], item[2])
        )
        return RollDecision(as_of, current, right.contract, reason, available, (left, right))

    def _activity_at(self, as_of: datetime) -> tuple[dict[MgcContract, ContractActivity], ...]:
        periods: dict[tuple[datetime, datetime], dict[MgcContract, ContractActivity]] = defaultdict(
            dict
        )
        for item in self.activity:
            if item.available_at <= as_of:
                previous = periods[item.period].get(item.contract)
                if previous is None or previous.available_at < item.available_at:
                    periods[item.period][item.contract] = item
        return tuple(
            periods[period]
            for period in sorted(periods, key=lambda item: (item[1], item[0]), reverse=True)
        )


def _rank_available(
    available: tuple[ContractListing, ...],
    cohorts: tuple[dict[MgcContract, ContractActivity], ...],
) -> tuple[MgcContract | None, tuple[ContractActivity, ...]]:
    if not available:
        return None, ()
    ordering = {item.contract: index for index, item in enumerate(available)}
    for cohort in cohorts:
        candidates = [item for contract, item in cohort.items() if contract in ordering]
        for measure in ("volume", "open_interest"):
            comparable = [
                (getattr(item, measure), item)
                for item in candidates
                if getattr(item, measure) is not None
            ]
            if len(comparable) >= 2:
                ranked = sorted(comparable, key=lambda item: (-item[0], ordering[item[1].contract]))
                return ranked[0][1].contract, tuple(item for _, item in ranked)
    return available[0].contract, ()


def _has_comparison(left: ContractActivity, right: ContractActivity, policy: RollPolicy) -> bool:
    return (left.volume is not None and right.volume is not None) or (
        policy.open_interest_lead_bps is not None
        and left.open_interest is not None
        and right.open_interest is not None
    )


def _lead(left: ContractActivity, right: ContractActivity, policy: RollPolicy) -> RollReason | None:
    if (
        left.volume is not None
        and right.volume is not None
        and right.volume * _BPS > left.volume * policy.volume_lead_bps
    ):
        return RollReason.VOLUME
    if (
        policy.open_interest_lead_bps is not None
        and left.open_interest is not None
        and right.open_interest is not None
        and right.open_interest * _BPS > left.open_interest * policy.open_interest_lead_bps
    ):
        return RollReason.OPEN_INTEREST
    return None


def _listing(item: object) -> ContractListing:
    if not isinstance(item, dict) or item.keys() != {
        "contract",
        "first_trade_at",
        "last_trade_at",
        "known_at",
    }:
        raise ValueError("invalid contract listing")
    return ContractListing(
        MgcContract(_string(item["contract"])),
        _time(item["first_trade_at"]),
        _time(item["last_trade_at"]),
        _time(item["known_at"]),
    )


def _activity(item: object) -> ContractActivity:
    required = {"contract", "period_start", "period_end", "available_at"}
    if not isinstance(item, dict) or not required <= item.keys() <= required | {
        "volume",
        "open_interest",
    }:
        raise ValueError("invalid contract activity")
    return ContractActivity(
        MgcContract(_string(item["contract"])),
        _time(item["period_start"]),
        _time(item["period_end"]),
        _time(item["available_at"]),
        _measure(item.get("volume")),
        _measure(item.get("open_interest")),
    )


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected contract text")
    return value


def _time(value: object) -> datetime:
    return minute_timestamp(_string(value))


def _measure(value: object) -> int | None:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise ValueError("contract activity measure must be an integer")
