"""Email delivery adapter.

``EMAIL_PROVIDER=none``  -> nothing is sent; callers learn that delivery is unavailable.
``EMAIL_PROVIDER=console`` -> development only: the message (including links) is logged locally.
``EMAIL_PROVIDER=smtp``  -> delivered through the configured SMTP server.

The adapter never pretends a message was sent.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("app.email")


@dataclass
class EmailResult:
    delivered: bool
    provider: str
    detail: str = ""


def _send_smtp(msg: EmailMessage) -> None:
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls()
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
        smtp.send_message(msg)


async def send_email(to: str, subject: str, text: str) -> EmailResult:
    s = get_settings()
    if s.email_provider == "none":
        log.warning("email_not_configured", extra={"subject": subject})
        return EmailResult(False, "none", "Email delivery is not configured.")
    if s.email_provider == "console":
        if s.is_production:
            return EmailResult(False, "console", "Console email is disabled in production.")
        # Development convenience only: printed to the local API log, not delivered.
        log.warning("dev_email_not_delivered", extra={"to_domain": to.split("@")[-1], "subject": subject, "body": text})
        return EmailResult(False, "console", "Development mode: message written to the API log, not delivered.")
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    try:
        await asyncio.to_thread(_send_smtp, msg)
        return EmailResult(True, "smtp")
    except Exception as exc:  # noqa: BLE001
        log.error("email_send_failed", extra={"error": type(exc).__name__})
        return EmailResult(False, "smtp", "Email delivery failed.")
