"""Telegram setup tests use fake HTTP responses and synthetic identifiers only."""

import json
import traceback
from dataclasses import dataclass, field
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from aurum import telegram_setup
from aurum.cli import main
from aurum.providers import NoRedirect
from aurum.telegram_setup import MAX_RESPONSE_BYTES, inspect_telegram

TOKEN = "123456:synthetic_private_token"
BOT = {"id": 123456, "is_bot": True, "username": "AurumSyntheticBot"}
WEBHOOK = {"url": "", "pending_update_count": 3}


def ok(result: object) -> dict[str, object]:
    return {"ok": True, "result": result}


@dataclass
class SyntheticTelegram:
    replies: list[Any]
    requests: list[Request] = field(default_factory=list)

    def open(self, request: Request, *, timeout: int) -> BytesIO:
        assert timeout == 10
        assert request.get_method() == "POST"
        assert request.full_url.startswith(f"https://api.telegram.org/bot{TOKEN}/")
        self.requests.append(request)
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return BytesIO(result if isinstance(result, bytes) else json.dumps(result).encode())


def configure(monkeypatch: pytest.MonkeyPatch, replies: list[Any]) -> SyntheticTelegram:
    client = SyntheticTelegram(replies)
    monkeypatch.setenv("AURUM_TELEGRAM_TOKEN", TOKEN)

    def opener(handler: object) -> SyntheticTelegram:
        assert handler is NoRedirect
        return client

    monkeypatch.setattr(telegram_setup, "build_opener", opener)
    return client


def test_inspection_deduplicates_private_chats_without_sending_or_acknowledging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 99, "type": "private"}, "text": TOKEN}},
        {"update_id": 11, "message": {"chat": {"id": -77, "type": "group"}}},
        {"update_id": 12, "message": {"chat": {"id": 99, "type": "private"}}},
        {"update_id": 13, "message": {"chat": {"id": 98, "type": "private"}}},
    ]
    client = configure(monkeypatch, [ok(BOT), ok(WEBHOOK), ok(updates)])
    result = inspect_telegram()
    assert result["private_chat_candidates"] == ["98", "99"]
    assert result["status"] == "select_private_chat"
    assert result["messages_sent"] == 0
    assert result["updates_acknowledged"] is False
    assert result["configuration_changed"] is False
    assert result["pending_updates_inspected"] == 4
    assert TOKEN not in json.dumps(result)
    assert [request.full_url.rsplit("/", 1)[1] for request in client.requests] == [
        "getMe",
        "getWebhookInfo",
        "getUpdates",
    ]
    parameters = client.requests[-1].data
    assert isinstance(parameters, bytes)
    assert json.loads(parameters) == {"limit": 20, "timeout": 0}


def test_configured_webhook_is_preserved_and_its_private_url_is_not_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhook = {"url": f"https://example.invalid/{TOKEN}", "pending_update_count": 4}
    client = configure(monkeypatch, [ok(BOT), ok(webhook)])
    result = inspect_telegram()
    assert result["status"] == "webhook_configured"
    assert result["webhook_configured"] is True
    assert result["private_chat_candidates"] == []
    assert len(client.requests) == 2
    assert TOKEN not in json.dumps(result)


def test_cli_reports_empty_page_without_creating_database_or_exposing_bot_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch, [ok(BOT), ok(WEBHOOK), ok([])])
    database = tmp_path / "unused-synthetic.sqlite"
    assert main(["--db", str(database), "telegram-inspect"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["status"] == "start_private_chat"
    assert TOKEN not in output.out
    assert not database.exists()


@pytest.mark.parametrize(
    "response",
    [
        HTTPError(f"https://example.invalid/{TOKEN}", 401, TOKEN, Message(), None),
        OSError(TOKEN),
        b"not JSON: synthetic_private_token",
        {"ok": False, "description": TOKEN},
        b"x" * (MAX_RESPONSE_BYTES + 1),
    ],
    ids=("http", "transport", "json", "provider", "oversized"),
)
def test_inspection_failures_redact_tokens_bodies_and_request_urls(
    monkeypatch: pytest.MonkeyPatch, response: object
) -> None:
    configure(monkeypatch, [response])
    with pytest.raises(ValueError, match="Telegram inspection failed") as failure:
        inspect_telegram()
    assert TOKEN not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize("stage", ("bot", "webhook", "updates", "chat"))
def test_invalid_metadata_is_rejected(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    replies = [ok(BOT), ok(WEBHOOK), ok([])]
    if stage == "bot":
        replies[0] = ok({**BOT, "id": True})
    elif stage == "webhook":
        replies[1] = ok({**WEBHOOK, "pending_update_count": False})
    elif stage == "updates":
        replies[2] = ok({"private": TOKEN})
    else:
        replies[2] = ok([{"message": {"chat": {"id": True, "type": "private"}}}])
    configure(monkeypatch, replies)
    with pytest.raises(ValueError, match="Telegram inspection failed"):
        inspect_telegram()


def test_missing_or_invalid_local_credential_does_not_open_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = configure(monkeypatch, [])
    monkeypatch.delenv("AURUM_TELEGRAM_TOKEN")
    with pytest.raises(ValueError, match="unset"):
        inspect_telegram()
    with pytest.raises(ValueError, match="environment variable name"):
        inspect_telegram(TOKEN)
    monkeypatch.setenv("AURUM_TELEGRAM_TOKEN", "invalid synthetic token")
    with pytest.raises(ValueError, match="valid bot token"):
        inspect_telegram()
    assert not client.requests
