"""Self-registration, email verification and password reset.

All four flows are public - they are the only routes besides /login and
/api/version that an anonymous visitor may reach.

Two rules run through this module:

* **No account enumeration.** "Forgot password" and a registration against an
  address that already exists both answer with the same page whether or not
  the account is there. The site is on the public internet, and the member
  list of a fire department is not something to hand out. The one exception is
  a taken *username*: the person has to pick another one, so that is reported.
* **Verifying an address does not grant access.** A new account stays disabled
  until an admin enables it in the user administration. Registration is open to
  anyone who can reach ffw-funk.slin.io, so the mail proves only that the
  address belongs to them - a human still decides who gets in.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.accounts import consume_token, issue_token, send_reset_mail, send_verification_mail
from app.db import get_db
from app.models import TOKEN_RESET, TOKEN_VERIFY, AppUser, utcnow
from app.security import MIN_PASSWORD_LENGTH, hash_password
from app.templating import render

router = APIRouter(tags=["account"])

DB = Annotated[AsyncSession, Depends(get_db)]

# Shown whether or not the address is known - see the module docstring.
GENERIC_SENT = (
    "Falls ein Konto zu dieser Adresse existiert, ist eine E-Mail unterwegs. Schau auch im Spam-Ordner nach."
)


def _password_problem(password: str, repeat: str) -> str | None:
    if password != repeat:
        return "Die Passwörter stimmen nicht überein."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein."
    return None


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
@router.get("/register")
async def register_page(request: Request) -> Response:
    return render(request, "register.html", {})


@router.post("/register")
async def do_register(
    request: Request,
    db: DB,
    username: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_repeat: Annotated[str, Form()],
) -> Response:
    username = username.strip()
    email = email.strip().lower()

    problem = _password_problem(password, password_repeat)
    if problem:
        return render(request, "register.html", {"error": problem, "username": username, "email": email})

    if not username or "@" not in email:
        return render(
            request,
            "register.html",
            {"error": "Bitte Benutzername und E-Mail-Adresse angeben.", "username": username},
        )

    if await db.scalar(select(AppUser).where(AppUser.username == username)) is not None:
        # A taken username has to be reported - the person needs another one.
        return render(
            request,
            "register.html",
            {"error": f"Der Benutzername '{username}' ist bereits vergeben.", "email": email},
        )

    if await db.scalar(select(AppUser).where(AppUser.email == email)) is not None:
        # A taken address is NOT reported: that would confirm who has an account.
        return render(request, "register.html", {"message": GENERIC_SENT})

    user = AppUser(
        username=username,
        password_hash=hash_password(password),
        role="VIEWER",
        enabled=False,  # an admin enables the account after verification
        email=email,
    )
    db.add(user)
    await db.flush()

    raw = await issue_token(db, user, TOKEN_VERIFY)
    await db.commit()

    await send_verification_mail(user, raw)
    return render(request, "register.html", {"message": GENERIC_SENT})


@router.get("/verify/{token}")
async def verify_email(token: str, request: Request, db: DB) -> Response:
    user = await consume_token(db, token, TOKEN_VERIFY)
    if user is None:
        await db.commit()
        return render(
            request,
            "account-result.html",
            {
                "title": "Link ungültig",
                "error": "Dieser Bestätigungslink ist abgelaufen oder wurde schon benutzt.",
            },
        )

    user.email_verified_at = utcnow()
    await db.commit()

    return render(
        request,
        "account-result.html",
        {
            "title": "E-Mail bestätigt",
            "message": (
                "Danke! Deine Adresse ist bestätigt. Ein Administrator schaltet "
                "deinen Zugang frei - danach kannst du dich anmelden."
            ),
        },
    )


# --------------------------------------------------------------------------- #
# Password reset
# --------------------------------------------------------------------------- #
@router.get("/forgot-password")
async def forgot_password_page(request: Request) -> Response:
    return render(request, "forgot-password.html", {})


@router.post("/forgot-password")
async def do_forgot_password(request: Request, db: DB, email: Annotated[str, Form()]) -> Response:
    user = await db.scalar(select(AppUser).where(AppUser.email == email.strip().lower()))

    # A disabled account gets no reset link: it cannot log in anyway, and
    # sending one would confirm that the address is known.
    if user is not None and user.enabled:
        raw = await issue_token(db, user, TOKEN_RESET)
        await db.commit()
        await send_reset_mail(user, raw)

    return render(request, "forgot-password.html", {"message": GENERIC_SENT})


@router.get("/reset-password/{token}")
async def reset_password_page(token: str, request: Request) -> Response:
    # The token is not checked here, only when the new password is submitted -
    # so a link that is merely previewed (by a mail client prefetching it, say)
    # is not burned before the person gets to use it.
    return render(request, "reset-password.html", {"token": token})


@router.post("/reset-password/{token}")
async def do_reset_password(
    token: str,
    request: Request,
    db: DB,
    password: Annotated[str, Form()],
    password_repeat: Annotated[str, Form()],
) -> Response:
    problem = _password_problem(password, password_repeat)
    if problem:
        return render(request, "reset-password.html", {"token": token, "error": problem})

    user = await consume_token(db, token, TOKEN_RESET)
    if user is None:
        await db.commit()
        return render(
            request,
            "account-result.html",
            {
                "title": "Link ungültig",
                "error": (
                    "Dieser Link ist abgelaufen oder wurde schon benutzt. "
                    "Fordere unter 'Passwort vergessen' einen neuen an."
                ),
            },
        )

    user.password_hash = hash_password(password)
    await db.commit()

    return render(
        request,
        "account-result.html",
        {
            "title": "Passwort gesetzt",
            "message": "Dein neues Passwort ist aktiv. Du kannst dich jetzt anmelden.",
        },
    )
