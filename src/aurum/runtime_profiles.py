import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from re import fullmatch
from typing import Any, Literal

from aurum.candles import Timeframe
from aurum.economic_events import EconomicCalendar
from aurum.exchange_calendar import SessionCalendar
from aurum.instruments import MgcContract
from aurum.market_context import ResearchSession
from aurum.market_data import DataKind, canonical_json
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG
from aurum.monitoring import MarketMonitor, MonitorConfig, describe_monitor
from aurum.notifications import DEFAULT_DELIVERY_PREFERENCES, Channel, DeliveryPreferences, Severity
from aurum.providers import FeedStatus
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, Variant
from aurum.streaming import HttpsGapBackfill, WebSocketSource

RUNTIME_PROFILE_VERSION = 1


def _validate_label(name: str) -> None:
    if not fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()-]{0,79}", name):
        raise ValueError("profile name must be a short plain label")


def _validate_env(*names: str | None) -> None:
    if any(name is not None and not fullmatch(r"AURUM_[A-Z0-9_]{1,80}", name) for name in names):
        raise ValueError("provide AURUM_ environment variable names, never credential values")


def _validate_reference(reference: str) -> None:
    if not fullmatch(r"[a-f0-9]{64}", reference):
        raise ValueError("profile references require a stored SHA-256 identifier")


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    name: str
    calendar_dataset_id: str
    endpoint_env: str
    source_status: FeedStatus
    token_env: str | None = None
    backfill_endpoint_env: str | None = None
    backfill_token_env: str | None = None
    timeframe: Timeframe = Timeframe.FIVE_MINUTES
    variant: Variant = DEFAULT_STRATEGY_CONFIG.variant
    stale_seconds: int = 90
    economic_calendar: dict[str, object] | None = None
    require_clear_event_risk: bool = False

    def __post_init__(self) -> None:
        _validate_label(self.name)
        _validate_reference(self.calendar_dataset_id)
        _validate_env(
            self.endpoint_env, self.token_env, self.backfill_endpoint_env, self.backfill_token_env
        )
        if self.source_status not in (
            FeedStatus.REAL_TIME,
            FeedStatus.DELAYED,
            FeedStatus.SIMULATED,
        ):
            raise ValueError("provider profile requires real-time, delayed or simulated data")
        if self.source_status is FeedStatus.SIMULATED and (
            self.token_env or self.backfill_endpoint_env
        ):
            raise ValueError("simulated provider profiles cannot use credentials or backfill")
        if self.backfill_token_env and not self.backfill_endpoint_env:
            raise ValueError("backfill token requires a backfill endpoint variable")
        if not 1 <= self.stale_seconds <= 3600:
            raise ValueError("provider stale threshold must be between 1 and 3600 seconds")

    def economic_events(self) -> EconomicCalendar | None:
        if self.economic_calendar is None:
            return None
        events = self.economic_calendar.get("events")
        if isinstance(events, list) and len(events) > 5_000:
            raise ValueError("provider snapshot supports at most 5000 economic announcements")
        return EconomicCalendar.from_dict(self.economic_calendar)


@dataclass(frozen=True, slots=True)
class DeliveryProfile:
    name: str
    monitor_id: str
    channel: Channel
    destination_env: str
    token_env: str | None = None
    preferences: DeliveryPreferences = DEFAULT_DELIVERY_PREFERENCES

    def __post_init__(self) -> None:
        _validate_label(self.name)
        _validate_reference(self.monitor_id)
        _validate_env(self.destination_env, self.token_env)
        if (self.channel is Channel.TELEGRAM) != (self.token_env is not None):
            raise ValueError(
                "Telegram requires a token variable and Discord uses its webhook variable"
            )
        for session in self.preferences.allowed_sessions:
            ResearchSession(session)
        if max(self.preferences.expiry_minutes, self.preferences.lease_seconds) > 10_080:
            raise ValueError("delivery expiry and lease settings exceed the local limits")


type RuntimeProfile = ProviderProfile | DeliveryProfile


def save_runtime_profile(store: DatasetStore, profile: RuntimeProfile) -> str:
    kind = "provider" if isinstance(profile, ProviderProfile) else "delivery"
    settings = asdict(profile)
    if isinstance(profile, ProviderProfile):
        _provider_calendar(store, profile)
        economic = profile.economic_events()
        if economic is None:
            del settings["economic_calendar"]
        else:
            settings["economic_calendar"] = economic.as_dict()
        if not profile.require_clear_event_risk:
            del settings["require_clear_event_risk"]
    else:
        status = describe_monitor(store, profile.monitor_id)
        definition = status["definition"]
        assert isinstance(definition, dict)
        if definition["monitor_config"]["source_status"] != FeedStatus.REAL_TIME:
            raise ValueError("external notification profiles require a real-time monitor")
    definition_json = canonical_json(
        {
            "runtime_profile_version": RUNTIME_PROFILE_VERSION,
            "kind": kind,
            "settings": settings,
        }
    )
    profile_id = sha256(definition_json.encode()).hexdigest()
    with store.connection(write=True) as db:
        db.execute(
            "INSERT INTO runtime_profiles VALUES (?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (profile_id, kind, definition_json, datetime.now(UTC).isoformat()),
        )
    return profile_id


def list_runtime_profiles(store: DatasetStore, limit: int = 100) -> list[dict[str, object]]:
    if not store.path.is_file():
        return []
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 9:
            return []
        return [
            {
                **json.loads(row["definition"]),
                "profile_id": row["id"],
                "created_at": row["created_at"],
            }
            for row in db.execute(
                "SELECT * FROM runtime_profiles ORDER BY created_at DESC, id LIMIT ?", (limit,)
            )
        ]


def describe_runtime_profile(store: DatasetStore, profile_id: str) -> dict[str, object]:
    with store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 9:
            row = db.execute(
                "SELECT * FROM runtime_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if row is not None:
                return {
                    **json.loads(row["definition"]),
                    "profile_id": profile_id,
                    "created_at": row["created_at"],
                }
    raise ValueError(f"unknown runtime profile: {profile_id}")


def _settings(
    store: DatasetStore, profile_id: str, kind: Literal["provider", "delivery"]
) -> dict[str, Any]:
    profile = describe_runtime_profile(store, profile_id)
    if profile["kind"] != kind or profile["runtime_profile_version"] != RUNTIME_PROFILE_VERSION:
        raise ValueError(f"incompatible {kind} profile version or kind")
    settings = profile["settings"]
    assert isinstance(settings, dict)
    return settings


def _provider_calendar(
    store: DatasetStore, profile: ProviderProfile
) -> tuple[SessionCalendar, MgcContract]:
    provenance = store.describe(profile.calendar_dataset_id)["provenance"]
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    if calendar.sessions is None:
        raise ValueError("provider profile requires a dated reviewed calendar")
    if (
        profile.source_status is not FeedStatus.SIMULATED
        and provenance["data_kind"] == DataKind.SYNTHETIC
    ):
        raise ValueError("real and delayed profiles require a non-synthetic calendar dataset")
    return calendar, MgcContract(str(provenance["contract"]))


def _env_value(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"required environment variable {name} is unset")
    return value


def provider_runtime(
    store: DatasetStore, profile_id: str
) -> tuple[MarketMonitor, WebSocketSource, HttpsGapBackfill | None]:
    raw = _settings(store, profile_id, "provider")
    profile = ProviderProfile(
        **{
            **raw,
            "source_status": FeedStatus(str(raw["source_status"])),
            "timeframe": Timeframe(str(raw["timeframe"])),
            "variant": Variant(str(raw["variant"])),
        }
    )
    calendar, contract = _provider_calendar(store, profile)
    source = WebSocketSource(
        _env_value(profile.endpoint_env), profile.source_status, profile.token_env
    )
    backfill = (
        HttpsGapBackfill(_env_value(profile.backfill_endpoint_env), profile.backfill_token_env)
        if profile.backfill_endpoint_env
        else None
    )
    monitor = MarketMonitor(
        store,
        calendar,
        contract,
        MonitorConfig(
            profile.name,
            profile.source_status,
            stale_seconds=profile.stale_seconds,
            timeframe=profile.timeframe,
            source_origin=source.origin,
            source_fingerprint=source.fingerprint,
        ),
        DEFAULT_STRUCTURE_CONFIG,
        replace(
            DEFAULT_STRATEGY_CONFIG,
            variant=profile.variant,
            require_clear_event_risk=profile.require_clear_event_risk,
        ),
        economic=profile.economic_events(),
    )
    return monitor, source, backfill


def load_delivery_profile(store: DatasetStore, profile_id: str) -> DeliveryProfile:
    raw = _settings(store, profile_id, "delivery")
    preferences = raw["preferences"]
    assert isinstance(preferences, dict)
    return DeliveryProfile(
        **{
            **raw,
            "channel": Channel(str(raw["channel"])),
            "preferences": DeliveryPreferences(
                **{
                    **preferences,
                    "allowed_sessions": tuple(preferences["allowed_sessions"]),
                    "severities": tuple(Severity(item) for item in preferences["severities"]),
                }
            ),
        }
    )
