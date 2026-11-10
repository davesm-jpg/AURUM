import json
from dataclasses import dataclass
from hashlib import sha256
from re import fullmatch
from typing import Self
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from aurum.instruments import MGC
from aurum.market_data import canonical_json
from aurum.notifications import Channel, DeliveryFailure
from aurum.providers import NoRedirect


@dataclass(frozen=True, slots=True, repr=False)
class HttpAlertChannel:
    channel: Channel
    destination_key: str
    _url: str
    _chat_id: str | None = None

    @classmethod
    def telegram(cls, token: str, chat_id: str) -> Self:
        if not fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not fullmatch(
            r"(?:-?[0-9]+|@[A-Za-z0-9_]+)", chat_id
        ):
            raise ValueError("invalid Telegram bot token or chat ID")
        return cls(
            Channel.TELEGRAM,
            _key(Channel.TELEGRAM, chat_id),
            f"https://api.telegram.org/bot{token}/sendMessage",
            chat_id,
        )

    @classmethod
    def discord(cls, webhook_url: str) -> Self:
        url = urlsplit(webhook_url)
        path = fullmatch(r"/api(?:/v[0-9]+)?/webhooks/([0-9]+)/[A-Za-z0-9_.-]+", url.path)
        if (
            url.scheme != "https"
            or url.netloc != "discord.com"
            or url.query
            or url.fragment
            or path is None
        ):
            raise ValueError("invalid Discord incoming webhook")
        return cls(Channel.DISCORD, _key(Channel.DISCORD, path[1]), f"{webhook_url}?wait=true")

    def send(self, alert: dict[str, object]) -> None:
        content = render_alert(alert)
        payload: dict[str, object]
        if self.channel is Channel.TELEGRAM:
            payload = {"chat_id": self._chat_id, "text": content}
        else:
            payload = {"content": content[:950], "allowed_mentions": {"parse": []}}
        request = Request(
            self._url,
            data=canonical_json(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirect).open(request, timeout=20) as response:
                result = json.loads(response.read(64 * 1024))
        except HTTPError as exc:
            retry = None
            if exc.code == 429 or 500 <= exc.code < 600:
                header = exc.headers.get("Retry-After", "")
                retry = min(int(header), 86_400) if header.isdigit() and len(header) <= 5 else 15
            raise DeliveryFailure(f"http_{exc.code}", retry_seconds=retry) from None
        except (OSError, ValueError):
            raise DeliveryFailure("unconfirmed_transport_result", uncertain=True) from None
        if not isinstance(result, dict):
            raise DeliveryFailure("unconfirmed_provider_response", uncertain=True)
        if self.channel is Channel.TELEGRAM:
            if result.get("ok") is not True:
                raise DeliveryFailure("telegram_rejected_request")
        elif not result.get("id"):
            raise DeliveryFailure("unconfirmed_provider_response", uncertain=True)


def _key(channel: Channel, address: str) -> str:
    return sha256(f"{channel}/{address}".encode()).hexdigest()


def render_alert(alert: dict[str, object]) -> str:
    contract = str(alert.get("contract", "MGC"))
    if alert.get("event") == "data_quality_warning":
        return (
            f"AURUM paper research • {contract}\n"
            f"Data quality: {alert.get('state')}\n"
            "Research signal delivery is suspended until the feed recovers."
        )
    setup = _mapping(alert.get("setup"))
    context = _mapping(setup.get("context"))
    plan = _mapping(setup.get("plan"))
    score = _mapping(setup.get("score"))
    sweep = _mapping(_mapping(setup.get("sweep")).get("sweep"))
    mss = _mapping(setup.get("mss"))
    zone = _mapping(setup.get("zone"))
    frames = context.get("higher")
    higher = (
        ", ".join(
            f"{frame.get('timeframe')} {frame.get('trend')}"
            for frame in frames
            if isinstance(frame, dict)
        )
        if isinstance(frames, list)
        else ""
    )
    cautions = setup.get("cautions")
    caution_text = (
        ", ".join(str(item).replace("_", " ") for item in cautions)
        if isinstance(cautions, list)
        else ""
    )
    observed_risk = _mapping(alert.get("observed_economic")).get("risk")
    if isinstance(observed_risk, str):
        observation = f"Event risk at observation {observed_risk}"
        caution_text = f"{caution_text}, {observation}" if caution_text else observation
    return "\n".join(
        (
            f"AURUM PAPER RESEARCH • {contract} • {setup.get('direction')}",
            f"New York {alert.get('new_york_at')} | Prague {alert.get('prague_at')}",
            f"Session {context.get('session')} | Higher frames {higher}",
            f"Sweep {_price(sweep.get('extreme_ticks'))} | "
            f"MSS {_price(mss.get('break_price_ticks'))}",
            f"Zone {_price(zone.get('lower_ticks'))}–{_price(zone.get('upper_ticks'))}",
            f"Entry {_price(plan.get('entry_ticks'))} | Stop {_price(plan.get('stop_ticks'))} | "
            f"Target {_price(plan.get('target_ticks'))} | Qty {plan.get('quantity')}",
            f"Gross risk {plan.get('gross_risk_cents')}¢ | "
            f"Gross reward {plan.get('gross_reward_cents')}¢ | "
            f"Net risk {plan.get('net_loss_cents')}¢",
            f"Experimental score {score.get('total')}/100 | Comparable samples "
            f"{score.get('validated_comparable_samples')}",
            f"Feed age {alert.get('feed_age_seconds')} s | {caution_text}",
        )
    )


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _price(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value * MGC.tick_size:.2f}"
    return "unavailable"
