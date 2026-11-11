import json
import smtplib
import ssl
from dataclasses import dataclass, field, replace
from email.message import EmailMessage
from pathlib import Path

import pytest
from test_notifications import AT, add_alert, fixture_monitor

from aurum.email_transport import SmtpAlertChannel, SmtpSettings
from aurum.notifications import DeliveryFailure, DeliveryWorker, delivery_status
from aurum.runtime_profiles import DeliveryProfile, load_delivery_profile, save_runtime_profile

SETTINGS = SmtpSettings(
    "smtp.example.invalid", 587, "synthetic-user", "aurum@example.invalid", "starttls"
)
SECRET = "synthetic-password-only"


@dataclass
class SyntheticSmtp:
    error: Exception | None = None
    quit_error: bool = False
    encrypted: bool = False
    authenticated: bool = False
    messages: list[EmailMessage] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    def __enter__(self) -> "SyntheticSmtp":
        return self

    def __exit__(self, *_: object) -> None:
        if self.quit_error:
            raise smtplib.SMTPServerDisconnected("synthetic post-acknowledgement disconnect")

    def ehlo(self) -> None:
        self.steps.append("ehlo")

    def starttls(self, *, context: ssl.SSLContext) -> None:
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        self.encrypted = True
        self.steps.append("tls")

    def login(self, username: str, password: str) -> None:
        assert self.encrypted
        assert username == SETTINGS.username and password == SECRET
        self.authenticated = True
        self.steps.append("login")

    def send_message(
        self, message: EmailMessage, *, from_addr: str, to_addrs: list[str]
    ) -> dict[str, tuple[int, bytes]]:
        assert self.authenticated and self.encrypted
        assert from_addr == SETTINGS.sender and to_addrs == ["recipient@example.invalid"]
        self.steps.append("send")
        if self.error is not None:
            raise self.error
        self.messages.append(message)
        return {}


def configure(monkeypatch: pytest.MonkeyPatch, client: SyntheticSmtp) -> None:
    def smtp(host: str, port: int, *, timeout: int) -> SyntheticSmtp:
        assert host == SETTINGS.host and port == 587 and timeout == 20
        return client

    def smtp_ssl(host: str, port: int, *, timeout: int, context: ssl.SSLContext) -> SyntheticSmtp:
        assert host == SETTINGS.host and port == 465 and timeout == 20
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        client.encrypted = True
        return client

    monkeypatch.setattr(smtplib, "SMTP", smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_ssl)


@pytest.mark.parametrize("implicit", (False, True))
def test_email_transport_encrypts_before_auth_and_accepts_acknowledged_message(
    monkeypatch: pytest.MonkeyPatch, implicit: bool
) -> None:
    client = SyntheticSmtp(quit_error=True)
    configure(monkeypatch, client)
    settings = replace(SETTINGS, tls="implicit", port=465) if implicit else SETTINGS
    channel = SmtpAlertChannel(settings, "recipient@example.invalid", SECRET)
    alert: dict[str, object] = {"alert_id": "synthetic-alert", "contract": "MGC-2025-04"}
    channel.send(alert)
    assert client.steps == (
        ["ehlo", "login", "send"] if implicit else ["ehlo", "tls", "ehlo", "login", "send"]
    )
    message = client.messages[0]
    assert message["Subject"] == "AURUM paper research signal"
    assert "AURUM PAPER RESEARCH" in message.get_content()
    assert SECRET not in message.as_string() and SECRET not in repr(channel)
    channel.send(alert)
    assert client.messages[1]["Message-ID"] == message["Message-ID"]
    assert (
        replace(channel, password="rotated-synthetic-secret").destination_key
        == channel.destination_key
    )


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (smtplib.SMTPDataError(451, b"synthetic temporarily rejected"), "retry"),
        (smtplib.SMTPDataError(550, b"synthetic rejected"), "failed"),
        (smtplib.SMTPServerDisconnected("synthetic lost acknowledgement"), "uncertain"),
    ],
)
def test_email_failures_follow_durable_delivery_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, state: str
) -> None:
    monitor = fixture_monitor(tmp_path)
    add_alert(monitor, "synthetic-email")
    client = SyntheticSmtp(error=error)
    configure(monkeypatch, client)
    channel = SmtpAlertChannel(SETTINGS, "recipient@example.invalid", SECRET)
    worker = DeliveryWorker(monitor.store, monitor.monitor_id, channel)
    assert worker.run_once(clock=lambda: AT) == 1
    (delivery,) = list(delivery_status(monitor.store, monitor.monitor_id))
    assert delivery["state"] == state
    assert SECRET not in json.dumps(delivery)
    assert worker.run_once(clock=lambda: AT) == 0


def test_email_profile_retains_only_environment_names(tmp_path: Path) -> None:
    monitor = fixture_monitor(tmp_path)
    from aurum.notifications import Channel

    profile = DeliveryProfile(
        "Synthetic email profile",
        monitor.monitor_id,
        Channel.EMAIL,
        "AURUM_EMAIL_TO",
        "AURUM_SMTP_PASSWORD",
        smtp_env="AURUM_SMTP_SETTINGS",
    )
    profile_id = save_runtime_profile(monitor.store, profile)
    assert load_delivery_profile(monitor.store, profile_id) == profile
    with monitor.store.connection() as db:
        saved = db.execute(
            "SELECT definition FROM runtime_profiles WHERE id = ?", (profile_id,)
        ).fetchone()[0]
    assert SECRET not in saved and SETTINGS.host not in saved
    with pytest.raises(ValueError, match="SMTP settings"):
        replace(profile, smtp_env=None)


@pytest.mark.parametrize(
    "value",
    [
        "recipient@example.invalid\nBcc:other@example.invalid",
        "a@example.invalid,b@example.invalid",
        "missing-domain",
    ],
)
def test_email_recipient_rejects_injection_and_multiple_destinations(value: str) -> None:
    with pytest.raises(ValueError):
        SmtpAlertChannel(SETTINGS, value, SECRET)


def test_email_refuses_authentication_when_tls_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SyntheticSmtp()
    configure(monkeypatch, client)

    def unsupported(**_: object) -> None:
        raise smtplib.SMTPNotSupportedError("synthetic server does not support TLS")

    monkeypatch.setattr(client, "starttls", unsupported)
    with pytest.raises(DeliveryFailure, match="smtp_tls_failed"):
        SmtpAlertChannel(SETTINGS, "recipient@example.invalid", SECRET).send(
            {"alert_id": "synthetic"}
        )
    assert not client.authenticated and not client.messages


def test_email_connection_failure_is_retryable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(*_: object, **__: object) -> None:
        raise OSError(f"synthetic error containing {SECRET}")

    monkeypatch.setattr(smtplib, "SMTP", refused)
    with pytest.raises(DeliveryFailure) as failure:
        SmtpAlertChannel(SETTINGS, "recipient@example.invalid", SECRET).send(
            {"alert_id": "synthetic"}
        )
    assert failure.value.reason == "smtp_connection_failed"
    assert failure.value.retry_seconds == 15 and not failure.value.uncertain
    assert SECRET not in str(failure.value)
