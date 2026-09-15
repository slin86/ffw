"""Form login, logout and the map page."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db import get_db
from app.models import AppUser
from app.security import AuthUser, verify_password
from app.sessions import get_session
from app.templating import render

router = APIRouter(tags=["web"])

DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/login")
async def login_page(request: Request, error: bool = False) -> Response:
    if get_session(request).get("username"):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {"error": error})


@router.post("/login")
async def do_login(
    request: Request,
    db: DB,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    user = await db.scalar(select(AppUser).where(AppUser.username == username))

    if user is None or not user.enabled or not verify_password(password, user.password_hash):
        return RedirectResponse("/login?error=true", status_code=303)

    session = get_session(request)
    session.clear()
    session.rotate()  # new session id on privilege change
    session["username"] = user.username
    session["role"] = user.role
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def do_logout(request: Request) -> Response:
    get_session(request).clear()
    return RedirectResponse("/login?logout", status_code=303)


@router.get("/")
async def map_page(request: Request, user: AuthUser) -> Response:
    return render(request, "map.html", {"username": user.username, "is_admin": user.is_admin})
