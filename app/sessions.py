"""Server-side sessions in Redis — the Spring Session Data Redis equivalent.

Starlette's built-in SessionMiddleware stores the whole session *in the cookie*,
signed but readable by the client, which is a different security model and can't
be invalidated server-side. So the session id here is an opaque random token and
all state lives in Redis under `SESSION:<sid>`, exactly like the Java version.
That also keeps horizontal scaling working: any replica can serve any request.

Kept deliberately small (one store protocol, two implementations) instead of
pulling in another dependency for ~80 lines of logic.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Protocol

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

SESSION_COOKIE = "SESSION"
_SESSION_PREFIX = "SESSION:"


class SessionStore(Protocol):
    async def read(self, sid: str) -> dict[str, Any] | None: ...
    async def write(self, sid: str, data: dict[str, Any], ttl: int) -> None: ...
    async def delete(self, sid: str) -> None: ...


class RedisSessionStore:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)

    async def read(self, sid: str) -> dict[str, Any] | None:
        raw = await self._redis.get(_SESSION_PREFIX + sid)
        return json.loads(raw) if raw else None

    async def write(self, sid: str, data: dict[str, Any], ttl: int) -> None:
        await self._redis.set(_SESSION_PREFIX + sid, json.dumps(data), ex=ttl)

    async def delete(self, sid: str) -> None:
        await self._redis.delete(_SESSION_PREFIX + sid)

    async def close(self) -> None:
        await self._redis.aclose()


class MemorySessionStore:
    """In-process store for tests. Not for multi-replica use."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def read(self, sid: str) -> dict[str, Any] | None:
        value = self._data.get(sid)
        return dict(value) if value is not None else None

    async def write(self, sid: str, data: dict[str, Any], ttl: int) -> None:
        self._data[sid] = dict(data)

    async def delete(self, sid: str) -> None:
        self._data.pop(sid, None)

    async def close(self) -> None:
        self._data.clear()


class Session:
    """Dict-like session bound to the request; tracks whether it changed."""

    __slots__ = ("_data", "dirty", "sid")

    def __init__(self, sid: str | None, data: dict[str, Any]) -> None:
        self.sid = sid
        self._data = data
        self.dirty = False

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if self._data.get(key) != value:
            self._data[key] = value
            self.dirty = True

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def pop(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            self.dirty = True
            return self._data.pop(key)
        return default

    def clear(self) -> None:
        if self._data:
            self._data.clear()
            self.dirty = True

    def rotate(self) -> None:
        """New session id, same contents — run this on login (fixation defence)."""
        self.sid = None
        self.dirty = True

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


class SessionMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        store: SessionStore,
        ttl: int = 8 * 60 * 60,
        secure: bool = False,
        cookie_name: str = SESSION_COOKIE,
    ) -> None:
        """`secure=True` means "Secure cookie on https requests", not "always"."""
        super().__init__(app)
        self.store = store
        self.ttl = ttl
        self.secure = secure
        self.cookie_name = cookie_name

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        sid = request.cookies.get(self.cookie_name)
        data = await self.store.read(sid) if sid else None
        if data is None:
            sid = None
            data = {}

        session = Session(sid, data)
        request.state.session = session

        response = await call_next(request)

        if session.sid is None and session.as_dict():
            # New session (or rotated): issue a fresh id and cookie.
            if sid:
                await self.store.delete(sid)
            session.sid = secrets.token_urlsafe(32)
            await self.store.write(session.sid, session.as_dict(), self.ttl)
            self._set_cookie(request, response, session.sid)
        elif session.sid is not None and not session.as_dict():
            await self.store.delete(session.sid)
            response.delete_cookie(self.cookie_name, path="/")
        elif session.sid is not None and session.dirty:
            await self.store.write(session.sid, session.as_dict(), self.ttl)

        return response

    def _set_cookie(self, request: Request, response: Response, sid: str) -> None:
        # The app is reachable over both https (public host) and plain http
        # (LAN host). A Secure cookie is silently dropped by the browser over
        # http, which costs the session and then fails CSRF on the next POST -
        # so derive the flag per request instead of pinning it in config.
        # `secure=False` still disables it everywhere, for local development.
        secure = self.secure and request.url.scheme == "https"
        response.set_cookie(
            self.cookie_name,
            sid,
            max_age=self.ttl,
            path="/",
            httponly=True,
            samesite="lax",
            secure=secure,
        )


def get_session(request: Request) -> Session:
    session: Session = request.state.session
    return session
