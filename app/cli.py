"""Small admin CLI.

The dev seeder only runs with APP_PROFILE=dev, so a production database starts
without any user and nobody can log in. This closes that gap without putting a
bootstrap account into the image or seeding one automatically, both of which
would be worse: a default password that nobody rotates is how these things end
up on the internet.

Usage inside the cluster:

    kubectl exec -n ffw-funk deploy/ffw-funk -- \\
        python -m app.cli create-user --username nils --role ADMIN

The password is read from stdin or prompted for, never passed as an argument -
command lines end up in shell history and in container logs.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.db import dispose_engine, get_sessionmaker
from app.models import ROLES, AppUser
from app.security import hash_password


async def create_user(username: str, password: str, role: str) -> int:
    if role not in ROLES:
        print(f"Unbekannte Rolle: {role}. Erlaubt: {', '.join(ROLES)}", file=sys.stderr)
        return 2

    async with get_sessionmaker()() as db:
        existing = await db.scalar(select(AppUser).where(AppUser.username == username))
        if existing is not None:
            print(f"Nutzer '{username}' existiert bereits.", file=sys.stderr)
            return 1

        db.add(
            AppUser(
                username=username,
                password_hash=hash_password(password),
                role=role,
                enabled=True,
            )
        )
        await db.commit()

    print(f"Nutzer '{username}' mit Rolle {role} angelegt.")
    return 0


async def set_password(username: str, password: str) -> int:
    async with get_sessionmaker()() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == username))
        if user is None:
            print(f"Nutzer '{username}' nicht gefunden.", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        await db.commit()

    print(f"Passwort für '{username}' gesetzt.")
    return 0


async def list_users() -> int:
    async with get_sessionmaker()() as db:
        users = list(await db.scalars(select(AppUser).order_by(AppUser.username)))

    if not users:
        print("Keine Nutzer vorhanden.")
        return 0
    for user in users:
        state = "aktiv" if user.enabled else "deaktiviert"
        print(f"{user.username:24} {user.role:8} {state}")
    return 0


def read_password(confirm: bool = True) -> str:
    """Read from a pipe when stdin is not a terminal, otherwise prompt."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")

    password = getpass.getpass("Passwort: ")
    if confirm and password != getpass.getpass("Passwort wiederholen: "):
        print("Passwörter stimmen nicht überein.", file=sys.stderr)
        raise SystemExit(2)
    return password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="Neuen Nutzer anlegen")
    create.add_argument("--username", required=True)
    create.add_argument("--role", default="ADMIN", choices=list(ROLES))

    passwd = sub.add_parser("set-password", help="Passwort eines Nutzers ändern")
    passwd.add_argument("--username", required=True)

    sub.add_parser("list-users", help="Alle Nutzer auflisten")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "create-user":
            password = read_password()
            if not password:
                print("Leeres Passwort ist nicht zulässig.", file=sys.stderr)
                return 2
            return asyncio.run(_run(create_user(args.username, password, args.role)))

        if args.command == "set-password":
            password = read_password()
            if not password:
                print("Leeres Passwort ist nicht zulässig.", file=sys.stderr)
                return 2
            return asyncio.run(_run(set_password(args.username, password)))

        return asyncio.run(_run(list_users()))
    except KeyboardInterrupt:
        return 130


async def _run(coro: object) -> int:
    try:
        return await coro  # type: ignore[misc, no-any-return]
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
