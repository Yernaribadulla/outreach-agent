from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any


class SendError(RuntimeError):
    """Safe provider error; never includes provider response text or credentials."""

    def __init__(self, message: str, category: str = "FAILED", uncertain: bool = False):
        super().__init__(message)
        self.category = category
        self.uncertain = uncertain


def simulated_send_enabled(env: dict[str, str]) -> bool:
    """SIMULATED_SEND is authoritative; SMTP_ENABLED remains a legacy opt-in."""
    flag = env.get("SIMULATED_SEND")
    if flag is not None:
        normalized = str(flag).strip().lower()
        return normalized != "false"  # missing/invalid values fail closed
    return str(env.get("SMTP_ENABLED", "false")).strip().lower() != "true"


def smtp_configuration_error(env: dict[str, str]) -> str | None:
    if simulated_send_enabled(env):
        return "Real SMTP is disabled while simulated mode is active."
    for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"):
        if not str(env.get(key, "")).strip():
            return "SMTP configuration is incomplete."
    try:
        port = int(env.get("SMTP_PORT", "587"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return "SMTP port is invalid."
    sender = parseaddr(str(env.get("SMTP_FROM", "")))[1]
    if not sender or "@" not in sender or any(ch.isspace() for ch in sender):
        return "SMTP sender address is invalid."
    for key in ("SMTP_USE_SSL", "SMTP_USE_TLS"):
        value = env.get(key)
        if value is not None and str(value).strip().lower() not in {"true", "false"}:
            return "SMTP security settings are invalid."
    use_ssl = str(env.get("SMTP_USE_SSL", "")).strip().lower() == "true" or ("SMTP_USE_SSL" not in env and port == 465)
    use_tls = str(env.get("SMTP_USE_TLS", "true" if not use_ssl else "false")).strip().lower() == "true"
    if not use_ssl and not use_tls:
        return "SMTP must use SSL or TLS."
    return None


def smtp_configuration_ready(env: dict[str, str]) -> bool:
    return smtp_configuration_error(env) is None


class EmailProvider:
    name = "abstract"

    def send(self, recipient: str, subject: str, body: str, html_body: str | None = None) -> None:
        raise NotImplementedError


class SimulatedProvider(EmailProvider):
    name = "simulated"

    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    def send(self, recipient: str, subject: str, body: str, html_body: str | None = None) -> None:
        self.sent.append({"recipient": recipient, "subject": subject, "body": body, "html_body": html_body})


class SMTPProvider(EmailProvider):
    name = "smtp"

    def __init__(self, env: dict[str, str]):
        self.env = dict(env)

    def _validated_settings(self) -> tuple[int, bool, bool]:
        reason = smtp_configuration_error(self.env)
        if reason:
            raise SendError(reason, "CONFIG")
        port = int(self.env.get("SMTP_PORT", "587"))
        use_ssl = str(self.env.get("SMTP_USE_SSL", "")).strip().lower() == "true"
        # Gmail's implicit TLS port is the safe default when SMTP_USE_SSL is omitted.
        if "SMTP_USE_SSL" not in self.env:
            use_ssl = port == 465
        use_tls = str(self.env.get("SMTP_USE_TLS", "true" if not use_ssl else "false")).strip().lower() == "true"
        return port, use_ssl, use_tls

    @staticmethod
    def _safe_failure(exc: Exception, during_delivery: bool = False) -> SendError:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return SendError("SMTP authentication failed.", "AUTH")
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            return SendError("SMTP provider rejected this recipient.", "RECIPIENT")
        if isinstance(exc, smtplib.SMTPResponseException):
            code = int(getattr(exc, "smtp_code", 0) or 0)
            response = str(getattr(exc, "smtp_error", b"")).lower()
            if code in {421, 429, 450, 451, 452} or any(word in response for word in ("rate limit", "too many", "throttl")):
                return SendError(f"SMTP provider rate limit/rejection (code {code}).", "RATE_LIMIT")
            return SendError(f"SMTP provider rejected the message (code {code}).", "REJECTION")
        if isinstance(exc, (TimeoutError, ConnectionError, OSError, smtplib.SMTPServerDisconnected)):
            return SendError("SMTP connection failed.", "CONNECTION", uncertain=during_delivery)
        return SendError("SMTP provider error.", "FAILED")

    def _connect(self):
        port, use_ssl, use_tls = self._validated_settings()
        server_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        server = server_class(self.env["SMTP_HOST"], port, timeout=20)
        try:
            if not use_ssl and use_tls:
                server.starttls()
            server.login(self.env["SMTP_USERNAME"], self.env["SMTP_PASSWORD"])
            return server
        except Exception as exc:
            try:
                server.quit()
            except Exception:
                pass
            raise self._safe_failure(exc) from None

    def check_available(self) -> None:
        """Check SMTP connectivity/authentication without submitting an email."""
        try:
            with self._connect() as server:
                server.noop()
        except SendError:
            raise
        except Exception as exc:
            raise self._safe_failure(exc) from None

    def send(self, recipient: str, subject: str, body: str, html_body: str | None = None) -> None:
        message = EmailMessage()
        message["From"] = self.env.get("SMTP_FROM", "")
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
        try:
            with self._connect() as server:
                server.send_message(message)
        except SendError:
            raise
        except Exception as exc:
            raise self._safe_failure(exc, during_delivery=True) from None
