"""Tests for the admin CLI (app/cli.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import cli
from app.models import AppUser
from app.security import verify_password


@pytest.fixture(autouse=True)
def _use_test_db(session_factory, monkeypatch):
    """Point the CLI at the test database instead of the configured one."""
    monkeypatch.setattr(cli, "get_sessionmaker", lambda: session_factory)

    async def noop() -> None:
        return None

    monkeypatch.setattr(cli, "dispose_engine", noop)


async def test_create_user_stores_a_bcrypt_hash(session_factory, seeded, capsys):
    assert await cli.create_user("nils", "geheim123", "ADMIN") == 0

    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "nils"))

    assert user is not None
    assert user.role == "ADMIN"
    assert user.enabled is True
    assert user.password_hash != "geheim123"
    assert user.password_hash.startswith("$2a$")
    assert verify_password("geheim123", user.password_hash)


async def test_create_user_refuses_a_duplicate(session_factory, seeded):
    assert await cli.create_user("admin", "egal", "ADMIN") == 1


async def test_create_user_refuses_an_unknown_role(session_factory, seeded):
    assert await cli.create_user("nils", "geheim123", "SUPERUSER") == 2

    async with session_factory() as db:
        assert await db.scalar(select(AppUser).where(AppUser.username == "nils")) is None


async def test_set_password_replaces_the_hash(session_factory, seeded):
    async with session_factory() as db:
        before = (await db.scalar(select(AppUser).where(AppUser.username == "admin"))).password_hash

    assert await cli.set_password("admin", "neues-passwort") == 0

    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "admin"))

    assert user.password_hash != before
    assert verify_password("neues-passwort", user.password_hash)
    assert not verify_password("admin", user.password_hash)


async def test_set_password_on_unknown_user(session_factory, seeded):
    assert await cli.set_password("gibtsnicht", "egal") == 1


async def test_list_users_prints_every_account(session_factory, seeded, capsys):
    assert await cli.list_users() == 0
    output = capsys.readouterr().out
    assert "admin" in output
    assert "viewer" in output
    assert "deaktiviert" in output  # the seeded, disabled account


async def test_new_user_can_actually_log_in(session_factory, seeded, anon):
    """End to end: a CLI-created account works against the real login form."""
    assert await cli.create_user("nils", "geheim123", "ADMIN") == 0

    page = await anon.get("/login")
    token = page.text.split('name="_csrf" value="')[1].split('"')[0]
    response = await anon.post("/login", data={"username": "nils", "password": "geheim123", "_csrf": token})
    assert response.status_code == 303

    map_page = await anon.get("/")
    assert 'name="_is_admin" content="true"' in map_page.text


def test_password_is_read_from_stdin_when_piped(monkeypatch):
    """Never take the password as an argument - it lands in shell history."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("aus-der-pipe\n"))
    assert cli.read_password() == "aus-der-pipe"


def test_parser_rejects_an_unknown_role():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["create-user", "--username", "x", "--role", "ROOT"])
