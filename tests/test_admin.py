"""Admin UI pages — flash messages, self-protection, form round trips."""

from __future__ import annotations


async def test_vehicle_list_renders(admin):
    response = await admin.get("/admin/vehicles")
    assert response.status_code == 200
    assert "HLF 20/1" in response.text
    assert "Frei auf Wache" in response.text


async def test_active_nav_entry_is_marked(admin):
    """The Java controllers never set currentPage, so the highlight never showed."""
    response = await admin.get("/admin/vehicles")
    assert '<li class="active"><a href="/admin/vehicles">' in response.text


async def test_create_vehicle_via_form(admin):
    response = await admin.post(
        "/admin/vehicles/create",
        data={"callsign": "RW 1/1", "type": "RW", "status": "2", "_csrf": _token(admin)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = await admin.get("/admin/vehicles")
    assert "RW 1/1" in listing.text
    assert "angelegt" in listing.text  # flash message


async def test_flash_message_is_shown_only_once(admin):
    await admin.post(
        "/admin/vehicles/create",
        data={"callsign": "RW 2/1", "type": "RW", "status": "2", "_csrf": _token(admin)},
    )
    assert "angelegt" in (await admin.get("/admin/vehicles")).text
    assert "angelegt" not in (await admin.get("/admin/vehicles")).text


async def test_duplicate_callsign_shows_an_error_flash(admin):
    await admin.post(
        "/admin/vehicles/create",
        data={"callsign": "HLF 20/1", "type": "HLF", "status": "2", "_csrf": _token(admin)},
    )
    listing = await admin.get("/admin/vehicles")
    assert "existiert bereits" in listing.text


async def test_edit_form_preselects_type_and_status(admin):
    response = await admin.get("/admin/vehicles/1/edit")
    assert response.status_code == 200
    assert '<option value="HLF" selected>' in response.text
    assert '<option value="2" selected>' in response.text


async def test_update_vehicle_via_form(admin):
    await admin.post(
        "/admin/vehicles/1",
        data={
            "callsign": "HLF 20/1",
            "type": "HLF",
            "status": "4",
            "lat": "53.60",
            "lng": "10.00",
            "_csrf": _token(admin),
        },
    )
    vehicle = next(v for v in (await admin.get("/api/vehicles")).json() if v["id"] == 1)
    assert vehicle["status"] == 4
    assert vehicle["lat"] == 53.60


async def test_admin_cannot_delete_their_own_account(admin, session_factory):
    admin_id = await _user_id(session_factory, "admin")

    await admin.post(f"/admin/users/{admin_id}/delete", data={"_csrf": _token(admin)})

    listing = await admin.get("/admin/users")
    assert "nicht löschen" in listing.text
    assert await _user_id(session_factory, "admin") == admin_id  # still there


async def test_admin_cannot_disable_their_own_account(admin, session_factory):
    # The admin's own row renders no toggle button at all.
    page = await admin.get("/admin/users")
    assert "eigenes Konto" in page.text

    # And the route refuses it even if the request is forged by hand.
    admin_id = await _user_id(session_factory, "admin")
    await admin.post(f"/admin/users/{admin_id}/toggle", data={"_csrf": _token(admin)})

    listing = await admin.get("/admin/users")
    assert "nicht deaktivieren" in listing.text


async def test_admin_may_disable_another_user(admin, session_factory):
    viewer_id = await _user_id(session_factory, "viewer")
    await admin.post(f"/admin/users/{viewer_id}/toggle", data={"_csrf": _token(admin)})
    listing = await admin.get("/admin/users")
    assert "deaktiviert" in listing.text


async def test_create_user_hashes_the_password(admin, session_factory):
    from sqlalchemy import select

    from app.models import AppUser
    from app.security import verify_password

    await admin.post(
        "/admin/users/create",
        data={
            "username": "neuer",
            "password": "geheim123",
            "role": "VIEWER",
            "_csrf": _token(admin),
        },
    )
    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == "neuer"))
    assert user is not None
    assert user.password_hash != "geheim123"
    assert verify_password("geheim123", user.password_hash)


async def test_station_form_round_trip(admin):
    response = await admin.post(
        "/admin/stations",
        data={
            "name": "Wache Harburg",
            "lat": "53.46",
            "lng": "9.98",
            "description": "Testwache",
            "_csrf": _token(admin),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = await admin.get("/admin/stations")
    assert "Wache Harburg" in listing.text
    assert "53.4600, 9.9800" in listing.text


async def test_incident_toggle_from_the_admin_list(admin):
    created = await admin.post("/api/incidents", json={"name": "Brand Hbf", "lat": 53.55, "lng": 9.99})
    incident_id = created.json()["id"]

    assert "Brand Hbf" in (await admin.get("/admin/incidents")).text

    await admin.post(f"/admin/incidents/{incident_id}/toggle", data={"_csrf": _token(admin)})
    assert "Brand Hbf" not in (await admin.get("/admin/incidents")).text
    assert "Brand Hbf" in (await admin.get("/admin/incidents?all=true")).text


async def test_new_forms_render_for_both_location_types(admin):
    assert (await admin.get("/admin/stations/new")).status_code == 200
    assert (await admin.get("/admin/incidents/new")).status_code == 200


async def test_edit_form_of_unknown_location_is_404(admin):
    assert (await admin.get("/admin/stations/999/edit")).status_code == 404
    assert (await admin.get("/admin/incidents/999/edit")).status_code == 404


def _token(client) -> str:
    from app.security import CSRF_HEADER

    return client.headers[CSRF_HEADER]


async def _user_id(session_factory, username: str) -> int | None:
    from sqlalchemy import select

    from app.models import AppUser

    async with session_factory() as db:
        user = await db.scalar(select(AppUser).where(AppUser.username == username))
    return user.id if user else None
