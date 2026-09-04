"""Test fixtures.

Runs against in-memory SQLite (aiosqlite) so the suite needs neither Postgres
nor Redis, mirroring the H2 + @WebMvcTest setup on the Java side. Sessions use
the in-process store.

The client fixtures log in through the real form login, so every test also
exercises session handling and the CSRF filter rather than stubbing the
security layer out.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("APP_PROFILE", "test")

import app.db as db_module
from app.main import create_app
from app.models import AppUser, Base, Station, Vehicle
from app.security import CSRF_HEADER, hash_password
from app.sessions import MemorySessionStore


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def seeded(session_factory):
    """One admin, one viewer, two vehicles, one station."""
    async with session_factory() as db:
        db.add_all(
            [
                AppUser(
                    username="admin",
                    password_hash=hash_password("admin"),
                    role="ADMIN",
                    enabled=True,
                ),
                AppUser(
                    username="viewer",
                    password_hash=hash_password("viewer"),
                    role="VIEWER",
                    enabled=True,
                ),
                AppUser(
                    username="gesperrt",
                    password_hash=hash_password("geheim"),
                    role="VIEWER",
                    enabled=False,
                ),
                Vehicle(callsign="HLF 20/1", type="HLF", status=2, lat=53.587, lng=10.044),
                Vehicle(callsign="DLK 12/1", type="DLK", status=1, lat=53.594, lng=9.990),
                Station(name="Feuerwache Wandsbek", lat=53.573, lng=10.077),
            ]
        )
        await db.commit()


@pytest.fixture
async def app_instance(session_factory, seeded):
    application = create_app(session_store=MemorySessionStore())

    async def override_get_db():
        async with session_factory() as session:
            yield session

    application.dependency_overrides[db_module.get_db] = override_get_db
    return application


@pytest.fixture
async def anon(app_instance) -> AsyncIterator[AsyncClient]:
    """Client without a session."""
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _login(client: AsyncClient, username: str, password: str) -> AsyncClient:
    """Log in the way a browser does, then keep the CSRF token on the client."""
    page = await client.get("/login")
    token = _extract_csrf_field(page.text)
    response = await client.post("/login", data={"username": username, "password": password, "_csrf": token})
    assert response.status_code == 303, f"Login für {username} fehlgeschlagen"

    # Fresh token: the session id is rotated on login.
    map_page = await client.get("/")
    client.headers[CSRF_HEADER] = _extract_csrf_meta(map_page.text)
    return client


def _extract_csrf_field(html: str) -> str:
    marker = 'name="_csrf" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def _extract_csrf_meta(html: str) -> str:
    marker = 'name="_csrf" content="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


@pytest.fixture
async def admin(app_instance) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield await _login(client, "admin", "admin")


@pytest.fixture
async def viewer(app_instance) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield await _login(client, "viewer", "viewer")
