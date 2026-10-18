"""Sending mail through the central relay.

The homelab runs one Postfix relay that fronts Amazon SES. Apps talk plain SMTP
to it from inside the cluster, with no credentials and no TLS, and never
configure a mail provider of their own - the relay is the single place that
knows about SES, its region and its data processing agreement.

Two properties matter for the callers:

* Sending is best effort. A notification is not worth failing a request over,
  so send_mail() logs and returns False instead of raising. Callers that do
  care can check the return value.
* smtplib is synchronous and blocking. One slow SMTP handshake would stall the
  whole event loop and with it every other request, so the actual send runs in
  a worker thread via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def build_message(
    settings: Settings,
    recipients: list[str],
    subject: str,
    body: str,
) -> EmailMessage:
    """Assemble a plain-text message.

    EmailMessage handles header encoding, so a subject with umlauts arrives
    intact rather than as mojibake.
    """
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    return message


def _send_blocking(settings: Settings, message: EmailMessage) -> None:
    """Runs in a worker thread - never call this from the event loop directly."""
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout) as smtp:
        smtp.send_message(message)


async def send_mail(
    recipients: list[str],
    subject: str,
    body: str,
    settings: Settings | None = None,
) -> bool:
    """Send one mail. Returns True on success, False on any failure.

    Never raises: a failed notification must not take down the request that
    triggered it.
    """
    settings = settings or get_settings()

    if not recipients:
        log.debug("Keine Empfänger, Mail wird nicht gesendet: %s", subject)
        return False

    if not settings.mail_enabled:
        # Normal on a dev machine: the relay is cluster-internal and would not
        # even resolve. The body is logged along with it so a verification or
        # reset link can be copied out of the log while developing - safe
        # precisely because this branch only runs with sending turned off.
        log.info("MAIL_ENABLED=false, Mail nicht gesendet: %s -> %s\n%s", subject, recipients, body)
        return False

    message = build_message(settings, recipients, subject, body)

    try:
        await asyncio.to_thread(_send_blocking, settings, message)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Mailversand fehlgeschlagen (%s): %s", subject, exc)
        return False

    log.info("Mail gesendet: %s -> %s", subject, recipients)
    return True
