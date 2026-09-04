"""Authentication, CSRF and authorisation.

Spring Security handed these three things over for free, so they are explicit
here. Two properties are preserved on purpose so the existing frontend and the
existing database keep working unchanged:

* BCrypt with the `$2a$` prefix Spring Security produced — existing password
  hashes in the `app_user` table verify without a reset.
* The CSRF header is `X-CSRF-TOKEN` (not `X-XSRF-TOKEN`), and the token plus
  header name are exposed as `_csrf` / `_csrf_header` meta tags, which is what
  map.js reads.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from urllib.parse import parse_qs

import bcrypt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app.db import get_db
from app.models import AppUser
from app.sessions import Session, get_session

CSRF_HEADER = "X-CSRF-TOKEN"
CSRF_FORM_FIELD = "_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(raw: str) -> str:
    """Produce a `$2a$` hash, byte-compatible with Spring Security's BCrypt."""
    hashed = bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=10, prefix=b"2a"))
    return hashed.decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
def csrf_token(session: Session) -> str:
    token = session.get(CSRF_FORM_FIELD)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_FORM_FIELD] = token
    return token


class CsrfMiddleware:
    """Reject unsafe requests without a matching token, like Spring's default.

    Written as raw ASGI rather than BaseHTTPMiddleware on purpose: reading the
    form body in a BaseHTTPMiddleware drains the receive channel, and the
    endpoint downstream then sees an empty body and answers 422. So the body is
    buffered here and replayed to the app.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        session: Session = scope["state"]["session"]
        expected = session.get(CSRF_FORM_FIELD)
        supplied = Headers(scope=scope).get(CSRF_HEADER)

        body = b""
        if supplied is None:
            body = await _read_body(receive)
            supplied = _token_from_form(Headers(scope=scope).get("content-type", ""), body)
            receive = _replay(body)

        if not expected or not supplied or not secrets.compare_digest(str(supplied), expected):
            response = JSONResponse({"error": "CSRF-Token ungültig oder fehlend"}, status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def _read_body(receive: Any) -> bytes:
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    return body


def _replay(body: bytes) -> Any:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _token_from_form(content_type: str, body: bytes) -> str | None:
    """Only urlencoded forms carry the token; the app posts no multipart data."""
    if not content_type.startswith("application/x-www-form-urlencoded"):
        return None
    values = parse_qs(body.decode("utf-8", errors="replace")).get(CSRF_FORM_FIELD)
    return values[0] if values else None


# --------------------------------------------------------------------------- #
# Authentication / authorisation
# --------------------------------------------------------------------------- #
class NotAuthenticated(Exception):
    """Raised instead of 401 so HTML routes can redirect to /login."""


class Forbidden(Exception):
    pass


async def load_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> AppUser | None:
    session = get_session(request)
    username = session.get("username")
    if not username:
        return None
    user = await db.scalar(select(AppUser).where(AppUser.username == username))
    if user is None or not user.enabled:
        session.clear()
        return None
    return user


CurrentUser = Annotated[AppUser | None, Depends(load_user)]


async def require_user(user: CurrentUser) -> AppUser:
    if user is None:
        raise NotAuthenticated
    return user


async def require_admin(user: Annotated[AppUser, Depends(require_user)]) -> AppUser:
    if not user.is_admin:
        raise Forbidden
    return user


AuthUser = Annotated[AppUser, Depends(require_user)]
AdminUser = Annotated[AppUser, Depends(require_admin)]
