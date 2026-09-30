from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


class SendError(RuntimeError): pass


class EmailProvider:
    name = "abstract"
    def send(self, recipient: str, subject: str, body: str, html_body: str | None = None) -> None: raise NotImplementedError


class SimulatedProvider(EmailProvider):
    name = "simulated"
    def __init__(self): self.sent = []
    def send(self, recipient: str, subject: str, body: str, html_body: str | None = None) -> None: self.sent.append({"recipient": recipient, "subject": subject, "body": body, "html_body": html_body})


class SMTPProvider(EmailProvider):
    name = "smtp"
    def __init__(self, env: dict[str, str]): self.env = env

    def check_available(self) -> None:
        """Check SMTP connectivity/authentication without submitting an email."""
        if self.env.get("SMTP_ENABLED", "false").lower() != "true": raise SendError("SMTP_DISABLED: real sending is disabled")
        required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"]
        if any(not self.env.get(key) for key in required): raise SendError("SMTP configuration is incomplete")
        port = int(self.env.get("SMTP_PORT", "587"))
        use_ssl = self.env.get("SMTP_USE_SSL", "false").lower() == "true"
        server_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with server_class(self.env["SMTP_HOST"], port, timeout=20) as server:
            if not use_ssl and self.env.get("SMTP_USE_TLS", "true").lower() == "true": server.starttls()
            server.login(self.env["SMTP_USERNAME"], self.env["SMTP_PASSWORD"])
            server.noop()

    def send(self, recipient: str, subject: str, body: str, html_body: str | None = None) -> None:
        if self.env.get("SMTP_ENABLED", "false").lower() != "true": raise SendError("SMTP_DISABLED: real sending is disabled")
        required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"]
        if any(not self.env.get(k) for k in required): raise SendError("SMTP configuration is incomplete")
        message = EmailMessage(); message["From"] = self.env["SMTP_FROM"]; message["To"] = recipient; message["Subject"] = subject; message.set_content(body)
        port = int(self.env.get("SMTP_PORT", "587"))
        use_ssl = self.env.get("SMTP_USE_SSL", "false").lower() == "true"
        server_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with server_class(self.env["SMTP_HOST"], port, timeout=20) as server:
            if not use_ssl and self.env.get("SMTP_USE_TLS", "true").lower() == "true": server.starttls()
            server.login(self.env["SMTP_USERNAME"], self.env["SMTP_PASSWORD"])
            if html_body:
                message.add_alternative(html_body, subtype="html")
            server.send_message(message)
