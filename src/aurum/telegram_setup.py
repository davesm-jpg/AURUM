"""Read-only Telegram onboarding without printing credentials or message bodies."""

import json
import os
from datetime import UTC, datetime
from re import fullmatch
from typing import Any, TypeGuard
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from aurum.market_data import canonical_json
from aurum.providers import NoRedirect

TELEGRAM_INSPECTION_VERSION = 1
MAX_RESPONSE_BYTES = 256 * 1024
UPDATE_LIMIT = 20


def _request(token: str, method: str, parameters: dict[str, object]) -> Any:
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=canonical_json(parameters).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        payload = json.loads(body)
    except HTTPError as exc:
        raise ValueError(f"Telegram inspection failed: http_{exc.code}") from None
    except (OSError, ValueError):
        raise ValueError("Telegram inspection failed: unreadable_response") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True or "result" not in payload:
        raise ValueError("Telegram inspection failed: rejected_or_invalid_response")
    return payload["result"]


def _positive_id(value: object) -> TypeGuard[int]:
    return type(value) is int and 0 < value < 2**63


def inspect_telegram(token_env: str = "AURUM_TELEGRAM_TOKEN") -> dict[str, object]:
    """Inspect a configured bot; never send, acknowledge updates or alter a webhook."""
    if fullmatch(r"[A-Z][A-Z0-9_]{0,127}", token_env) is None:
        raise ValueError("Telegram token variable must be an environment variable name")
    token = os.environ.get(token_env)
    if not token:
        raise ValueError("Telegram token variable is unset")
    if len(token) > 256 or fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) is None:
        raise ValueError("Telegram token variable does not contain a valid bot token")
    bot = _request(token, "getMe", {})
    if (
        not isinstance(bot, dict)
        or bot.get("is_bot") is not True
        or not _positive_id(bot.get("id"))
    ):
        raise ValueError("Telegram inspection failed: invalid_bot_identity")
    username = bot.get("username")
    if username is not None and (
        not isinstance(username, str) or fullmatch(r"[A-Za-z0-9_]{5,32}", username) is None
    ):
        raise ValueError("Telegram inspection failed: invalid_bot_identity")
    webhook = _request(token, "getWebhookInfo", {})
    if (
        not isinstance(webhook, dict)
        or not isinstance(webhook.get("url"), str)
        or type(webhook.get("pending_update_count")) is not int
        or not 0 <= webhook["pending_update_count"] < 2**63
    ):
        raise ValueError("Telegram inspection failed: invalid_webhook_metadata")
    configured = bool(webhook["url"])
    result: dict[str, object] = {
        "telegram_inspection_version": TELEGRAM_INSPECTION_VERSION,
        "bot_id": bot["id"],
        "bot_username": username,
        "webhook_configured": configured,
        "pending_update_count": webhook["pending_update_count"],
        "private_chat_candidates": [],
        "updates_acknowledged": False,
        "messages_sent": 0,
        "configuration_changed": False,
    }
    if configured:
        result.update(status="webhook_configured", pending_updates_inspected=0)
    else:
        # No offset: Telegram does not acknowledge these pending updates. Do
        # not set allowed_updates either, since that preference is persistent.
        updates = _request(token, "getUpdates", {"limit": UPDATE_LIMIT, "timeout": 0})
        if not isinstance(updates, list) or len(updates) > UPDATE_LIMIT:
            raise ValueError("Telegram inspection failed: invalid_update_page")
        candidates: set[int] = set()
        for update in updates:
            if not isinstance(update, dict):
                raise ValueError("Telegram inspection failed: invalid_update_page")
            message = update.get("message")
            chat = message.get("chat") if isinstance(message, dict) else None
            if isinstance(chat, dict) and chat.get("type") == "private":
                identifier = chat.get("id")
                if not _positive_id(identifier):
                    raise ValueError("Telegram inspection failed: invalid_private_chat")
                candidates.add(identifier)
        result.update(
            status="select_private_chat" if candidates else "start_private_chat",
            private_chat_candidates=[str(identifier) for identifier in sorted(candidates)],
            pending_updates_inspected=len(updates),
            candidate_scope="first_20_pending_updates_only",
        )
    result["checked_at"] = datetime.now(UTC).isoformat()
    return result
