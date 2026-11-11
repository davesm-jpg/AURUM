"""Authenticated TLS email delivery with conservative acknowledgement handling."""

import json
import smtplib
import ssl
from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from hashlib import sha256
from re import fullmatch
from typing import Self

from aurum.market_data import canonical_json
from aurum.notification_transport import render_alert
from aurum.notifications import Channel, DeliveryFailure


@dataclass(frozen=True, slots=True, repr=False)
class SmtpSettings:
    host: str
    port: int
    username: str
    sender: str
    tls: str

    def __post_init__(self) -> None:
        if not fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.host):
            raise ValueError("SMTP host must be a hostname without credentials or a URL")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        if self.tls not in ("starttls", "implicit"):
            raise ValueError("SMTP delivery requires starttls or implicit TLS")
        if (
            not self.username
            or not self.username.isascii()
            or any(char in self.username for char in "\r\n\0")
        ):
            raise ValueError("SMTP authentication username is required")
        _address(self.sender)

    @classmethod
    def from_json(cls, raw: str) -> Self:
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.keys() != {"host", "port", "username", "sender", "tls"}
            or any(
                not isinstance(value[field], str) for field in ("host", "username", "sender", "tls")
            )
        ):
            raise ValueError("invalid SMTP settings document")
        return cls(**value)


@dataclass(frozen=True, slots=True, repr=False)
class SmtpAlertChannel:
    settings: SmtpSettings
    recipient: str
    password: str
    channel: Channel = field(default=Channel.EMAIL, init=False)

    def __post_init__(self) -> None:
        _address(self.recipient)
        if not self.password or not self.password.isascii():
            raise ValueError("SMTP requires an ASCII authentication password")

    @property
    def destination_key(self) -> str:
        route = (
            self.channel,
            self.settings.host,
            self.settings.port,
            self.settings.username,
            self.settings.sender,
            self.recipient,
        )
        return sha256(canonical_json(route).encode()).hexdigest()

    def send(self, alert: dict[str, object]) -> None:
        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = self.recipient
        message["Subject"] = {
            "forward_paper_report": "AURUM forward paper research report",
            "data_quality_warning": "AURUM data quality warning",
        }.get(str(alert.get("event")), "AURUM paper research signal")
        identity = canonical_json((alert.get("alert_id"), self.destination_key)).encode()
        message["Message-ID"] = f"<{sha256(identity).hexdigest()}@aurum.local>"
        message.set_content(render_alert(alert))
        submitted = accepted = False
        context = ssl.create_default_context()
        try:
            client = (
                smtplib.SMTP_SSL(
                    self.settings.host, self.settings.port, timeout=20, context=context
                )
                if self.settings.tls == "implicit"
                else smtplib.SMTP(self.settings.host, self.settings.port, timeout=20)
            )
            with client:
                client.ehlo()
                if self.settings.tls == "starttls":
                    client.starttls(context=context)
                    client.ehlo()
                client.login(self.settings.username, self.password)
                submitted = True
                refused = client.send_message(
                    message, from_addr=self.settings.sender, to_addrs=[self.recipient]
                )
                if refused:
                    code = refused.get(self.recipient, (550, b""))[0]
                    raise _response_failure(code)
                accepted = True
        except smtplib.SMTPRecipientsRefused as exc:
            raise _response_failure(exc.recipients.get(self.recipient, (550, b""))[0]) from None
        except smtplib.SMTPResponseException as exc:
            if not accepted:
                raise _response_failure(exc.smtp_code) from None
        except (ssl.SSLError, smtplib.SMTPNotSupportedError):
            if not accepted:
                raise DeliveryFailure("smtp_tls_failed") from None
        except (OSError, smtplib.SMTPException):
            if not accepted:
                raise DeliveryFailure(
                    "smtp_unconfirmed_result" if submitted else "smtp_connection_failed",
                    uncertain=submitted,
                    retry_seconds=None if submitted else 15,
                ) from None


def _response_failure(code: int) -> DeliveryFailure:
    return DeliveryFailure(f"smtp_{code}", retry_seconds=15 if 400 <= code < 500 else None)


def _address(value: str) -> None:
    try:
        value.encode("ascii")
        address = Address(addr_spec=value)
    except (ValueError, UnicodeError, HeaderParseError) as exc:
        raise ValueError("SMTP addresses must be individual ASCII mailboxes") from exc
    if not address.domain or not address.username or any(char in value for char in "\r\n\0"):
        raise ValueError("SMTP addresses require one mailbox with a domain")
