"""Tests for the mail relay integration (app/mail.py).

No SMTP server is started: _send_blocking is the seam, so the tests assert on
the assembled message and on the failure behaviour instead.
"""

from __future__ import annotations

import smtplib

import pytest
from pydantic import ValidationError

from app import mail
from app.config import Settings


def make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "MAIL_ENABLED": True,
        "SMTP_HOST": "mail-relay.mail.svc.cluster.local",
        "SMTP_PORT": 587,
        "SMTP_FROM": "noreply@slin.io",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def test_defaults_point_at_the_central_relay():
    settings = Settings()
    assert settings.smtp_host == "mail-relay.mail.svc.cluster.local"
    assert settings.smtp_port == 587
    assert settings.smtp_from.endswith("@slin.io")


def test_sending_is_off_by_default():
    """A dev machine cannot resolve the relay, so it must not try."""
    assert Settings().mail_enabled is False


@pytest.mark.parametrize("sender", ["noreply@example.com", "nils@gmail.com", "noreply@slin.io.evil"])
def test_sender_outside_slin_io_is_rejected_at_startup(sender):
    """The relay refuses foreign senders - fail loudly at boot, not at send time."""
    with pytest.raises(ValidationError):
        Settings(SMTP_FROM=sender)


# --------------------------------------------------------------------------- #
# Message assembly
# --------------------------------------------------------------------------- #
def test_message_carries_sender_recipients_and_body():
    message = build = mail.build_message(
        make_settings(), ["a@slin.io", "b@slin.io"], "Betreff", "Zeile eins\nZeile zwei"
    )
    assert build["From"] == "noreply@slin.io"
    assert message["To"] == "a@slin.io, b@slin.io"
    assert message["Subject"] == "Betreff"
    assert "Zeile zwei" in message.get_content()


def test_umlauts_survive_in_subject_and_body():
    message = mail.build_message(
        make_settings(), ["a@slin.io"], "Neuer Einsatzort: Übung Groß Borstel", "Lösch?"
    )
    # Header may be RFC 2047 encoded on the wire; what matters is it decodes back.
    assert str(message["Subject"]) == "Neuer Einsatzort: Übung Groß Borstel"
    assert "Lösch?" in message.get_content()


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
async def test_send_mail_hands_the_message_to_smtp(monkeypatch):
    sent = {}

    def fake_send(settings, message):
        sent["host"] = settings.smtp_host
        sent["port"] = settings.smtp_port
        sent["to"] = message["To"]

    monkeypatch.setattr(mail, "_send_blocking", fake_send)

    assert await mail.send_mail(["a@slin.io"], "Betreff", "Text", settings=make_settings()) is True
    assert sent == {
        "host": "mail-relay.mail.svc.cluster.local",
        "port": 587,
        "to": "a@slin.io",
    }


async def test_nothing_is_sent_when_mail_is_disabled(monkeypatch):
    def fail(settings, message):
        raise AssertionError("darf nicht aufgerufen werden")

    monkeypatch.setattr(mail, "_send_blocking", fail)
    settings = make_settings(MAIL_ENABLED=False)
    assert await mail.send_mail(["a@slin.io"], "Betreff", "Text", settings=settings) is False


async def test_empty_recipient_list_is_a_no_op(monkeypatch):
    def fail(settings, message):
        raise AssertionError("darf nicht aufgerufen werden")

    monkeypatch.setattr(mail, "_send_blocking", fail)
    assert await mail.send_mail([], "Betreff", "Text", settings=make_settings()) is False


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPException("relay abgelehnt"),
        smtplib.SMTPRecipientsRefused({}),
        OSError("Name or service not known"),
        TimeoutError("relay antwortet nicht"),
    ],
)
async def test_a_failing_relay_never_raises(monkeypatch, error):
    """A notification must not take down the request that triggered it."""

    def fake_send(settings, message):
        raise error

    monkeypatch.setattr(mail, "_send_blocking", fake_send)
    assert await mail.send_mail(["a@slin.io"], "Betreff", "Text", settings=make_settings()) is False


async def test_send_does_not_block_the_event_loop(monkeypatch):
    """smtplib is blocking; a slow relay must not stall every other request."""
    import asyncio
    import threading

    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def fake_send(settings, message):
        seen["thread"] = threading.get_ident()

    monkeypatch.setattr(mail, "_send_blocking", fake_send)

    ticks = 0

    async def keep_ticking():
        nonlocal ticks
        for _ in range(3):
            await asyncio.sleep(0)
            ticks += 1

    await asyncio.gather(
        mail.send_mail(["a@slin.io"], "Betreff", "Text", settings=make_settings()),
        keep_ticking(),
    )

    assert seen["thread"] != loop_thread, "SMTP lief im Event-Loop statt im Worker-Thread"
    assert ticks == 3, "Event-Loop war blockiert"


async def test_disabled_mail_logs_the_body_so_links_are_recoverable(monkeypatch, caplog):
    """With sending off, the dev still needs the verification link."""
    import logging

    def fail(settings, message):
        raise AssertionError("darf nicht aufgerufen werden")

    monkeypatch.setattr(mail, "_send_blocking", fail)
    settings = make_settings(MAIL_ENABLED=False)

    with caplog.at_level(logging.INFO, logger="app.mail"):
        await mail.send_mail(
            ["a@slin.io"], "Betreff", "Link: https://example.test/verify/abc123", settings=settings
        )

    assert "https://example.test/verify/abc123" in caplog.text
