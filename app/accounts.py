"""One-time tokens for email verification and password reset.

Design decisions worth knowing before changing anything here:

* **Only the hash is stored.** The raw token leaves the process once, inside
  the mail. A dump of `user_token` therefore does not let anyone take over an
  account - the same reasoning that puts `password_hash` in `app_user` rather
  than a password. SHA-256 without a salt is right here (unlike for passwords):
  the input is 256 bits of entropy from `secrets`, so there is nothing to
  brute-force or rainbow-table.
* **Issuing invalidates earlier tokens of the same purpose.** Ask for a reset
  three times and only the newest link works, so an older mail sitting in an
  inbox is not a second key.
* **Lookup is by hash, never by user.** A token proves who it belongs to; the
  caller does not say.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.mail import send_mail
from app.models import (
    RESET_TTL_HOURS,
    TOKEN_RESET,
    TOKEN_VERIFY,
    VERIFY_TTL_HOURS,
    AppUser,
    UserToken,
    utcnow,
)

log = logging.getLogger(__name__)

TTL_HOURS = {TOKEN_VERIFY: VERIFY_TTL_HOURS, TOKEN_RESET: RESET_TTL_HOURS}


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def issue_token(db: AsyncSession, user: AppUser, purpose: str) -> str:
    """Create a token, store its hash, and return the raw value exactly once."""
    if purpose not in TTL_HOURS:
        raise ValueError(f"Unbekannter Token-Zweck: {purpose}")

    # Supersede anything still outstanding for this purpose.
    await db.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user.id,
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )

    raw = secrets.token_urlsafe(32)
    db.add(
        UserToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            purpose=purpose,
            expires_at=utcnow() + timedelta(hours=TTL_HOURS[purpose]),
        )
    )
    await db.flush()
    return raw


async def consume_token(db: AsyncSession, raw: str, purpose: str) -> AppUser | None:
    """Validate and burn a token. Returns the owner, or None if unusable.

    Unusable covers unknown, wrong purpose, expired and already used - all
    indistinguishable to the caller on purpose, so a wrong link cannot be
    probed for which of those it was.
    """
    token = await db.scalar(select(UserToken).where(UserToken.token_hash == hash_token(raw)))

    if token is None or token.purpose != purpose or not token.is_usable():
        return None

    token.used_at = utcnow()
    await db.flush()
    return token.user


# --------------------------------------------------------------------------- #
# Mails
# --------------------------------------------------------------------------- #
def _link(settings: Settings, path: str, raw_token: str) -> str:
    return f"{settings.public_base_url}{path}/{raw_token}"


async def send_verification_mail(user: AppUser, raw_token: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if user.email is None:
        return False

    body = (
        f"Hallo {user.username},\n\n"
        "für dich wurde ein Zugang zur FF-Trainingskarte angelegt.\n"
        "Bitte bestätige deine E-Mail-Adresse über diesen Link:\n\n"
        f"{_link(settings, '/verify', raw_token)}\n\n"
        f"Der Link ist {VERIFY_TTL_HOURS} Stunden gültig.\n\n"
        "Nach der Bestätigung schaltet ein Administrator deinen Zugang frei. "
        "Du bekommst keine weitere Mail - probiere die Anmeldung einfach später "
        "noch einmal.\n\n"
        "Wenn du dich nicht registriert hast, kannst du diese Mail ignorieren.\n"
    )
    return await send_mail([user.email], "FF-Trainingskarte: E-Mail bestätigen", body, settings)


async def send_reset_mail(user: AppUser, raw_token: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if user.email is None:
        return False

    body = (
        f"Hallo {user.username},\n\n"
        "für dein Konto wurde ein neues Passwort angefordert.\n"
        "Über diesen Link kannst du eines setzen:\n\n"
        f"{_link(settings, '/reset-password', raw_token)}\n\n"
        f"Der Link ist {RESET_TTL_HOURS} Stunde gültig und funktioniert nur einmal.\n\n"
        "Wenn du das nicht warst, passiert nichts - dein Passwort bleibt "
        "unverändert, solange der Link nicht benutzt wird.\n"
    )
    return await send_mail([user.email], "FF-Trainingskarte: Passwort zurücksetzen", body, settings)
