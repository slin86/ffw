"""Jinja2 setup and the small helpers that replace Thymeleaf idioms.

Thymeleaf gave `@{...}` URLs, automatic CSRF hidden fields and `#temporals`
formatting. Those become a `csrf_field()` global, a `datetime` filter and plain
string paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.config import BASE_DIR
from app.models import STATUS_LABELS
from app.security import CSRF_FORM_FIELD, csrf_token
from app.sessions import get_session

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def format_datetime(value: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    return value.strftime(fmt) if value else ""


def format_coords(lat: float, lng: float) -> str:
    return f"{lat:.4f}, {lng:.4f}"


templates.env.filters["datetime"] = format_datetime
templates.env.globals["status_labels"] = STATUS_LABELS
templates.env.globals["format_coords"] = format_coords


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render a template with the per-request extras every page needs.

    Flash messages are popped here, which is the same read-once behaviour the
    controllers implemented by hand with `session.removeAttribute(...)`.
    """
    session = get_session(request)
    token = csrf_token(session)
    ctx: dict[str, Any] = {
        "csrf_token": token,
        "csrf_field": Markup(f'<input type="hidden" name="{CSRF_FORM_FIELD}" value="{token}"/>'),
        "csrf_header": "X-CSRF-TOKEN",
        "flash_message": session.pop("flash_message", None),
        "flash_error": session.pop("flash_error", None),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


def flash(request: Request, message: str, *, error: bool = False) -> None:
    get_session(request)["flash_error" if error else "flash_message"] = message
