"""Login, session, CSRF and role rules — what SecurityConfig used to encode."""

from __future__ import annotations

import pytest

from app.security import CSRF_HEADER, hash_password, verify_password
from app.sessions import SESSION_COOKIE


async def test_bcrypt_hashes_use_the_spring_2a_prefix():
    """Existing hashes in app_user must keep verifying after the port."""
    hashed = hash_password("admin")
    assert hashed.startswith("$2a$")
    assert verify_password("admin", hashed)
    assert not verify_password("falsch", hashed)


@pytest.mark.parametrize(
    "plaintext,hashed",
    [
        ("", "$2a$06$DCq7YPn5Rq63x1Lad4cll.TV4S6ytwfsfvkgY8jIucDrjc8deX1s."),
        ("a", "$2a$06$m0CrhHm10qJ3lXRY.5zDGO3rS2KdeeWLuGmsfGlMfOxih58VYVfxe"),
        ("abc", "$2a$06$If6bvum7DFjUnE9p2uDeDu0YHzrHM6tf.iqN8.yx.jNN1ILEf7h0i"),
        (
            "abcdefghijklmnopqrstuvwxyz",
            "$2a$06$.rCVZVOThsIa97pEDOxvGuRRgzG64bvtJ0938xuqzv18d3ZpQhstC",
        ),
    ],
)
async def test_verifies_hashes_from_another_bcrypt_implementation(plaintext, hashed):
    """jBCrypt test vectors — the library Spring Security's BCryptPasswordEncoder uses.

    This is the check that the existing app_user rows survive the port: hashes
    written by the Java app must verify here without a password reset.
    """
    assert verify_password(plaintext, hashed)
    assert not verify_password(plaintext + "x", hashed)


async def test_anonymous_browser_is_redirected_to_login(anon):
    response = await anon.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_anonymous_api_call_gets_401_not_a_redirect(anon):
    response = await anon.get("/api/vehicles")
    assert response.status_code == 401


async def test_login_sets_the_session_cookie(anon):
    page = await anon.get("/login")
    token = page.text.split('name="_csrf" value="')[1].split('"')[0]
    response = await anon.post("/login", data={"username": "admin", "password": "admin", "_csrf": token})
    assert response.status_code == 303
    assert SESSION_COOKIE in anon.cookies


async def test_login_with_wrong_password_is_rejected(anon):
    page = await anon.get("/login")
    token = page.text.split('name="_csrf" value="')[1].split('"')[0]
    response = await anon.post("/login", data={"username": "admin", "password": "falsch", "_csrf": token})
    assert response.headers["location"] == "/login?error=true"


async def test_disabled_user_cannot_log_in(anon):
    page = await anon.get("/login")
    token = page.text.split('name="_csrf" value="')[1].split('"')[0]
    response = await anon.post("/login", data={"username": "gesperrt", "password": "geheim", "_csrf": token})
    assert response.headers["location"] == "/login?error=true"


async def test_unsafe_request_without_csrf_token_is_rejected(admin):
    admin.headers.pop(CSRF_HEADER)
    response = await admin.put("/api/vehicles/1/status", json={"status": 1})
    assert response.status_code == 403


async def test_wrong_csrf_token_is_rejected(admin):
    admin.headers[CSRF_HEADER] = "nicht-das-richtige-token"
    response = await admin.put("/api/vehicles/1/status", json={"status": 1})
    assert response.status_code == 403


async def test_logout_clears_the_session(admin):
    response = await admin.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    follow_up = await admin.get("/", follow_redirects=False)
    assert follow_up.status_code == 303


async def test_map_page_marks_admins_and_viewers_differently(admin, viewer):
    admin_page = await admin.get("/")
    viewer_page = await viewer.get("/")
    assert 'name="_is_admin" content="true"' in admin_page.text
    assert 'name="_is_admin" content="false"' in viewer_page.text
    # The admin gear icon must not be rendered for a viewer.
    assert "/admin/vehicles" in admin_page.text
    assert "/admin/vehicles" not in viewer_page.text


async def test_viewer_is_denied_the_admin_area(viewer):
    for path in ("/admin/vehicles", "/admin/users", "/admin/stations", "/admin/incidents"):
        response = await viewer.get(path)
        assert response.status_code == 403, path


@pytest.mark.parametrize(
    "password",
    [
        "simple",
        "pass@word",  # @ would split host from userinfo
        "has%20percent",  # would be decoded to "has percent"
        "slash/and:colon#hash",
        "quote\"and'apostrophe",
    ],
)
def test_db_url_survives_special_characters_in_the_password(password):
    """A mangled password shows up only as "password authentication failed"."""
    from sqlalchemy.engine import make_url

    from app.config import Settings

    settings = Settings(
        DB_URL="postgresql+asyncpg://db.example:5432/ffw_funk",
        DB_USER="ffw_funk",
        DB_PASSWORD=password,
    )
    parsed = make_url(settings.sqlalchemy_url)
    assert parsed.password == password
    assert parsed.username == "ffw_funk"
    assert parsed.host == "db.example"
    assert parsed.database == "ffw_funk"


async def test_secure_cookie_is_dropped_on_plain_http(app_instance):
    """The app is served over https (public) and http (LAN) at the same time.

    A Secure cookie is discarded by the browser over http, which loses the
    session and makes the next POST fail CSRF. So the flag follows the request
    scheme rather than a fixed config value.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.sessions import MemorySessionStore, SessionMiddleware

    application = create_app(session_store=MemorySessionStore())
    application.dependency_overrides = app_instance.dependency_overrides
    for middleware in application.user_middleware:
        if middleware.cls is SessionMiddleware:  # type: ignore[comparison-overlap]
            middleware.kwargs["secure"] = True

    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://lan.example") as http:
        assert "secure" not in (await http.get("/login")).headers["set-cookie"].lower()

    async with AsyncClient(transport=transport, base_url="https://public.example") as https:
        assert "secure" in (await https.get("/login")).headers["set-cookie"].lower()
