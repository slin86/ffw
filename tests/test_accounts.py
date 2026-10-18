"""Registration, email verification and password reset.

These run against the real routes with the real middleware, so CSRF and
sessions are exercised too. Mail is captured instead of sent.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import accounts, mail
from app.models import (
    TOKEN_RESET,
    TOKEN_VERIFY,
    AppUser,
    UserToken,
    utcnow,
)
from app.security import verify_password


@pytest.fixture
def outbox(monkeypatch):
    """Capture every mail the app tries to send."""
    sent: list[dict[str, object]] = []

    async def fake_send(recipients, subject, body, settings=None):
        sent.append({"to": recipients, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(accounts, "send_mail", fake_send)
    monkeypatch.setattr(mail, "send_mail", fake_send)
    return sent


def csrf_of(html: str) -> str:
    return html.split('name="_csrf" value="')[1].split('"')[0]


async def register(client, **overrides):
    """Fill in the registration form the way a browser would."""
    page = await client.get("/register")
    data = {
        "username": "kamerad",
        "email": "kamerad@example.com",
        "password": "ein-langes-passwort",
        "password_repeat": "ein-langes-passwort",
        "_csrf": csrf_of(page.text),
    }
    data.update(overrides)
    return await client.post("/register", data=data)


def link_token(body: str, path: str) -> str:
    """Pull the raw token out of the link in a mail body."""
    for word in body.split():
        if path in word:
            return word.rsplit("/", 1)[1]
    raise AssertionError(f"Kein {path}-Link in der Mail:\n{body}")


# --------------------------------------------------------------------------- #
# Token mechanics
# --------------------------------------------------------------------------- #
async def test_only_the_hash_is_stored(session_factory, seeded):
    """A dump of user_token must not let anyone take over an account."""
    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "admin"))
        raw = await accounts.issue_token(db, user, TOKEN_RESET)
        await db.commit()

        token = await db.scalar(select(UserToken))

    assert token.token_hash != raw
    assert token.token_hash == accounts.hash_token(raw)
    assert len(token.token_hash) == 64


async def test_a_token_works_exactly_once(session_factory, seeded):
    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "admin"))
        raw = await accounts.issue_token(db, user, TOKEN_RESET)
        await db.commit()

    async with session_factory() as db:
        assert (await accounts.consume_token(db, raw, TOKEN_RESET)) is not None
        await db.commit()

    async with session_factory() as db:
        assert (await accounts.consume_token(db, raw, TOKEN_RESET)) is None


async def test_issuing_a_new_token_kills_the_previous_one(session_factory, seeded):
    """An older reset mail in the inbox must not stay a second key."""
    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "admin"))
        first = await accounts.issue_token(db, user, TOKEN_RESET)
        second = await accounts.issue_token(db, user, TOKEN_RESET)
        await db.commit()

    async with session_factory() as db:
        assert (await accounts.consume_token(db, first, TOKEN_RESET)) is None
    async with session_factory() as db:
        assert (await accounts.consume_token(db, second, TOKEN_RESET)) is not None


async def test_an_expired_token_is_rejected(session_factory, seeded):
    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "admin"))
        raw = await accounts.issue_token(db, user, TOKEN_RESET)
        token = await db.scalar(select(UserToken))
        token.expires_at = utcnow() - timedelta(minutes=1)
        await db.commit()

    async with session_factory() as db:
        assert (await accounts.consume_token(db, raw, TOKEN_RESET)) is None


async def test_a_token_cannot_be_used_for_the_other_purpose(session_factory, seeded):
    """A verification link must not double as a password reset."""
    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "admin"))
        raw = await accounts.issue_token(db, user, TOKEN_VERIFY)
        await db.commit()

    async with session_factory() as db:
        assert (await accounts.consume_token(db, raw, TOKEN_RESET)) is None


async def test_an_unknown_token_is_rejected(session_factory, seeded):
    async with session_factory() as db:
        assert (await accounts.consume_token(db, "frei-erfunden", TOKEN_RESET)) is None


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
async def test_registration_creates_a_disabled_viewer(anon, session_factory, outbox):
    response = await register(anon)
    assert response.status_code == 200

    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "kamerad"))

    assert user is not None
    assert user.role == "VIEWER"
    assert user.enabled is False, "Selbstregistrierung darf keinen Zugang freischalten"
    assert user.email == "kamerad@example.com"
    assert user.email_verified_at is None


async def test_registration_sends_a_verification_mail(anon, outbox):
    await register(anon)
    assert len(outbox) == 1
    assert outbox[0]["to"] == ["kamerad@example.com"]
    assert "/verify/" in outbox[0]["body"]


async def test_a_new_account_cannot_log_in_before_an_admin_enables_it(anon, outbox):
    await register(anon)
    token = link_token(outbox[0]["body"], "/verify/")

    verified = await anon.get(f"/verify/{token}")
    assert "bestätigt" in verified.text

    page = await anon.get("/login")
    response = await anon.post(
        "/login",
        data={
            "username": "kamerad",
            "password": "ein-langes-passwort",
            "_csrf": csrf_of(page.text),
        },
    )
    assert response.headers["location"] == "/login?error=true"


async def test_verification_marks_the_address_and_burns_the_link(anon, session_factory, outbox):
    await register(anon)
    token = link_token(outbox[0]["body"], "/verify/")

    await anon.get(f"/verify/{token}")

    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "kamerad"))
    assert user.email_verified_at is not None

    again = await anon.get(f"/verify/{token}")
    assert "ungültig" in again.text


async def test_a_taken_username_is_reported(anon, seeded, outbox):
    response = await register(anon, username="admin")
    assert "bereits vergeben" in response.text
    assert outbox == []


async def test_a_taken_address_is_not_revealed(anon, session_factory, outbox):
    """Confirming who has an account would leak the member list."""
    await register(anon)
    first_text = (await anon.get("/register")).text  # noqa: F841

    second = await register(anon, username="jemand-anders")
    assert "bereits" not in second.text
    assert "unterwegs" in second.text

    async with session_factory() as db:
        count = len(list(await db.scalars(select(AppUser).where(AppUser.username == "jemand-anders"))))
    assert count == 0, "Es darf kein zweites Konto zur gleichen Adresse entstehen"
    assert len(outbox) == 1, "Für die doppelte Adresse darf keine Mail rausgehen"


async def test_mismatched_passwords_are_rejected(anon, session_factory, outbox):
    response = await register(anon, password_repeat="etwas-anderes")
    assert "stimmen nicht überein" in response.text

    async with session_factory() as db:
        assert (await db.scalar(select(AppUser).where(AppUser.username == "kamerad"))) is None


async def test_a_short_password_is_rejected(anon, session_factory, outbox):
    response = await register(anon, password="kurz", password_repeat="kurz")
    assert "mindestens" in response.text

    async with session_factory() as db:
        assert (await db.scalar(select(AppUser).where(AppUser.username == "kamerad"))) is None


async def test_registration_without_csrf_is_refused(anon, outbox):
    response = await anon.post(
        "/register",
        data={
            "username": "angreifer",
            "email": "a@example.com",
            "password": "ein-langes-passwort",
            "password_repeat": "ein-langes-passwort",
        },
    )
    assert response.status_code == 403
    assert outbox == []


# --------------------------------------------------------------------------- #
# Password reset
# --------------------------------------------------------------------------- #
@pytest.fixture
async def member(anon, session_factory, outbox):
    """A registered, verified and admin-enabled account."""
    await register(anon)
    token = link_token(outbox[0]["body"], "/verify/")
    await anon.get(f"/verify/{token}")

    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "kamerad"))
        user.enabled = True
        await db.commit()

    outbox.clear()
    return "kamerad@example.com"


async def request_reset(client, email):
    page = await client.get("/forgot-password")
    return await client.post("/forgot-password", data={"email": email, "_csrf": csrf_of(page.text)})


async def test_reset_mail_contains_a_working_link(anon, member, outbox, session_factory):
    await request_reset(anon, member)
    assert len(outbox) == 1
    token = link_token(outbox[0]["body"], "/reset-password/")

    page = await anon.get(f"/reset-password/{token}")
    response = await anon.post(
        f"/reset-password/{token}",
        data={
            "password": "neues-langes-passwort",
            "password_repeat": "neues-langes-passwort",
            "_csrf": csrf_of(page.text),
        },
    )
    assert "Passwort gesetzt" in response.text

    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "kamerad"))
    assert verify_password("neues-langes-passwort", user.password_hash)
    assert not verify_password("ein-langes-passwort", user.password_hash)


async def test_the_new_password_actually_works_for_login(anon, member, outbox):
    await request_reset(anon, member)
    token = link_token(outbox[0]["body"], "/reset-password/")

    page = await anon.get(f"/reset-password/{token}")
    await anon.post(
        f"/reset-password/{token}",
        data={
            "password": "neues-langes-passwort",
            "password_repeat": "neues-langes-passwort",
            "_csrf": csrf_of(page.text),
        },
    )

    login_page = await anon.get("/login")
    response = await anon.post(
        "/login",
        data={
            "username": "kamerad",
            "password": "neues-langes-passwort",
            "_csrf": csrf_of(login_page.text),
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_an_unknown_address_gets_the_same_answer(anon, outbox):
    """Otherwise the form tells an attacker which addresses have accounts."""
    known = await request_reset(anon, "kamerad@example.com")
    unknown = await request_reset(anon, "niemand@example.com")
    assert "unterwegs" in known.text
    assert known.text == unknown.text
    assert outbox == []


async def test_a_disabled_account_gets_no_reset_link(anon, session_factory, outbox):
    await register(anon)
    outbox.clear()

    response = await request_reset(anon, "kamerad@example.com")
    assert "unterwegs" in response.text
    assert outbox == [], "Ein gesperrtes Konto darf keinen Reset-Link bekommen"


async def test_a_reset_link_works_only_once(anon, member, outbox):
    await request_reset(anon, member)
    token = link_token(outbox[0]["body"], "/reset-password/")

    page = await anon.get(f"/reset-password/{token}")
    await anon.post(
        f"/reset-password/{token}",
        data={
            "password": "neues-langes-passwort",
            "password_repeat": "neues-langes-passwort",
            "_csrf": csrf_of(page.text),
        },
    )

    second = await anon.post(
        f"/reset-password/{token}",
        data={
            "password": "noch-ein-passwort",
            "password_repeat": "noch-ein-passwort",
            "_csrf": csrf_of(page.text),
        },
    )
    assert "ungültig" in second.text


async def test_opening_the_link_does_not_burn_it(anon, member, outbox, session_factory):
    """Mail clients prefetch links; a preview must not invalidate the token."""
    await request_reset(anon, member)
    token = link_token(outbox[0]["body"], "/reset-password/")

    await anon.get(f"/reset-password/{token}")
    await anon.get(f"/reset-password/{token}")

    async with session_factory() as db:
        stored = await db.scalar(select(UserToken).where(UserToken.purpose == TOKEN_RESET))
    assert stored.used_at is None


async def test_a_bad_password_does_not_burn_the_link(anon, member, outbox):
    await request_reset(anon, member)
    token = link_token(outbox[0]["body"], "/reset-password/")

    page = await anon.get(f"/reset-password/{token}")
    rejected = await anon.post(
        f"/reset-password/{token}",
        data={"password": "kurz", "password_repeat": "kurz", "_csrf": csrf_of(page.text)},
    )
    assert "mindestens" in rejected.text

    retry = await anon.post(
        f"/reset-password/{token}",
        data={
            "password": "jetzt-aber-lang-genug",
            "password_repeat": "jetzt-aber-lang-genug",
            "_csrf": csrf_of(page.text),
        },
    )
    assert "Passwort gesetzt" in retry.text


async def test_the_login_page_links_to_both_flows(anon):
    page = await anon.get("/login")
    assert "/forgot-password" in page.text
    assert "/register" in page.text
