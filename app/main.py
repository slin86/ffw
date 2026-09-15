"""Application entry point.

Started with `uvicorn app.main:create_app --factory`. There is deliberately no
module-level `app = create_app()`: that would open a Redis client as an import
side effect, which breaks `alembic` and the test suite.

Middleware order matters and is the reverse of the order added: SessionMiddleware
must run *outside* CsrfMiddleware, because the CSRF check reads the session.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, get_settings
from app.db import dispose_engine
from app.routers import locations as api_locations
from app.routers import misc, vehicles, web
from app.routers.admin import locations as admin_locations
from app.routers.admin import vehicles_users as admin_vehicles_users
from app.security import CsrfMiddleware, Forbidden, NotAuthenticated
from app.sessions import RedisSessionStore, SessionMiddleware, SessionStore

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.is_dev:
        from app.seed import seed_dev_data

        await seed_dev_data()
    yield
    store = getattr(app.state, "session_store", None)
    if store is not None:
        await store.close()
    await dispose_engine()


def create_app(session_store: SessionStore | None = None) -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="FF Trainingskarte",
        version=misc.APP_VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # Redis is the only production store; the in-memory one exists for tests.
    store = session_store or RedisSessionStore(settings.redis_url)
    app.state.session_store = store

    # Added last => runs first.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(
        SessionMiddleware,
        store=store,
        ttl=settings.session_ttl_seconds,
        secure=settings.session_cookie_secure,
    )

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    # The Spring app served static files from the context root; keep those paths
    # so the templates and map.js need no rewriting.
    app.mount("/css", StaticFiles(directory=str(BASE_DIR / "static" / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(BASE_DIR / "static" / "js")), name="js")

    app.include_router(web.router)
    app.include_router(vehicles.router)
    app.include_router(api_locations.stations)
    app.include_router(api_locations.incidents)
    app.include_router(misc.checkin)
    app.include_router(misc.geocode)
    app.include_router(misc.version)
    app.include_router(admin_vehicles_users.vehicles)
    app.include_router(admin_vehicles_users.users)
    app.include_router(admin_locations.stations)
    app.include_router(admin_locations.incidents)

    register_exception_handlers(app)
    return app


def register_exception_handlers(app: FastAPI) -> None:
    def wants_json(request: Request) -> bool:
        return request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", "")

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(request: Request, exc: NotAuthenticated) -> Response:
        """API callers get 401; browsers get sent to the login form."""
        if wants_json(request):
            return JSONResponse({"error": "Nicht angemeldet"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden) -> Response:
        return JSONResponse({"error": "Zugriff verweigert"}, status_code=403)

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException) -> Response:
        """Errors as a JSON body with an `error` key, as the frontend expects."""
        return JSONResponse(
            {"error": exc.detail, "status": exc.status_code},
            status_code=exc.status_code,
            headers=exc.headers,
        )
