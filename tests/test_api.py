"""/api/stations, /api/incidents, check-in and the geocode proxy."""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.routers import misc
from app.routers.misc import parse_nominatim


# --------------------------------------------------------------------------- #
# Stations
# --------------------------------------------------------------------------- #
async def test_station_json_carries_location_type(viewer):
    """The Java version emitted `locationType`, so map.js always read undefined."""
    stations = (await viewer.get("/api/stations")).json()
    assert stations[0]["location_type"] == "STATION"


async def test_station_lists_its_vehicles_without_recursing(admin):
    await admin.patch("/api/vehicles/1/location", json={"locationId": 1})
    station = (await admin.get("/api/stations")).json()[0]
    assert [v["callsign"] for v in station["vehicles"]] == ["HLF 20/1"]
    assert "location" not in station["vehicles"][0]  # no cycle in the payload


async def test_station_crud_is_admin_only(viewer):
    payload = {"name": "Wache 2", "lat": 53.55, "lng": 9.99}
    assert (await viewer.post("/api/stations", json=payload)).status_code == 403
    assert (await viewer.put("/api/stations/1", json=payload)).status_code == 403
    assert (await viewer.delete("/api/stations/1")).status_code == 403


async def test_admin_creates_and_updates_a_station(admin):
    created = await admin.post("/api/stations", json={"name": "Wache 2", "lat": 53.55, "lng": 9.99})
    assert created.status_code == 201
    station_id = created.json()["id"]

    updated = await admin.put(
        f"/api/stations/{station_id}",
        json={"name": "Wache 2 neu", "lat": 53.56, "lng": 9.98, "description": "Neubau"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Wache 2 neu"
    assert updated.json()["description"] == "Neubau"


async def test_unknown_station_is_404(admin):
    assert (await admin.get("/api/stations/999")).status_code == 404
    assert (
        await admin.put("/api/stations/999", json={"name": "x", "lat": 53.5, "lng": 9.9})
    ).status_code == 404


async def test_deleting_a_station_sets_its_vehicles_to_unterwegs(admin):
    await admin.patch("/api/vehicles/1/location", json={"locationId": 1})
    assert (await admin.delete("/api/stations/1")).status_code == 204

    vehicle = next(v for v in (await admin.get("/api/vehicles")).json() if v["id"] == 1)
    assert vehicle["location"] is None


# --------------------------------------------------------------------------- #
# Incidents
# --------------------------------------------------------------------------- #
async def test_incidents_default_to_active_only(admin):
    await admin.post("/api/incidents", json={"name": "Brand A", "lat": 53.55, "lng": 9.99})
    inactive = await admin.post(
        "/api/incidents", json={"name": "Brand B", "lat": 53.55, "lng": 9.99, "active": False}
    )
    assert inactive.json()["active"] is False

    assert [i["name"] for i in (await admin.get("/api/incidents")).json()] == ["Brand A"]
    assert len((await admin.get("/api/incidents", params={"all": "true"})).json()) == 2


async def test_new_incident_is_active_by_default(admin):
    created = await admin.post("/api/incidents", json={"name": "Brand C", "lat": 53.55, "lng": 9.99})
    assert created.json()["active"] is True
    assert created.json()["location_type"] == "INCIDENT"


async def test_toggle_active_is_admin_only(admin, viewer):
    created = await admin.post("/api/incidents", json={"name": "Brand D", "lat": 53.55, "lng": 9.99})
    incident_id = created.json()["id"]

    assert (
        await viewer.patch(f"/api/incidents/{incident_id}/active", json={"active": False})
    ).status_code == 403

    toggled = await admin.patch(f"/api/incidents/{incident_id}/active", json={"active": False})
    assert toggled.status_code == 200
    assert toggled.json()["active"] is False
    assert (await admin.get("/api/incidents")).json() == []


async def test_patch_active_without_the_field_is_a_422(admin):
    created = await admin.post("/api/incidents", json={"name": "Brand E", "lat": 53.55, "lng": 9.99})
    response = await admin.patch(f"/api/incidents/{created.json()['id']}/active", json={})
    assert response.status_code == 422


async def test_station_and_incident_endpoints_do_not_mix(admin):
    """Both live in one table; the discriminator must keep them apart."""
    await admin.post("/api/incidents", json={"name": "Brand F", "lat": 53.55, "lng": 9.99})
    assert [s["name"] for s in (await admin.get("/api/stations")).json()] == ["Feuerwache Wandsbek"]
    assert [i["name"] for i in (await admin.get("/api/incidents")).json()] == ["Brand F"]


# --------------------------------------------------------------------------- #
# Check-in
# --------------------------------------------------------------------------- #
async def test_checkin_reports_no_content_when_not_checked_in(viewer):
    assert (await viewer.get("/api/checkin/me")).status_code == 204


async def test_checkin_then_me_returns_the_vehicle(viewer):
    response = await viewer.post("/api/vehicles/1/checkin")
    assert response.status_code == 201
    assert response.json() == {"vehicleId": 1}
    assert (await viewer.get("/api/checkin/me")).json() == {"vehicleId": 1}


async def test_a_second_checkin_replaces_the_first(viewer):
    await viewer.post("/api/vehicles/1/checkin")
    await viewer.post("/api/vehicles/2/checkin")
    assert (await viewer.get("/api/checkin/me")).json() == {"vehicleId": 2}


async def test_checkout_is_idempotent(viewer):
    await viewer.post("/api/vehicles/1/checkin")
    assert (await viewer.post("/api/vehicles/1/checkout")).status_code == 204
    assert (await viewer.post("/api/vehicles/1/checkout")).status_code == 204
    assert (await viewer.get("/api/checkin/me")).status_code == 204


async def test_checkout_of_another_vehicle_does_nothing(viewer):
    await viewer.post("/api/vehicles/1/checkin")
    await viewer.post("/api/vehicles/2/checkout")
    assert (await viewer.get("/api/checkin/me")).json() == {"vehicleId": 1}


async def test_checkin_on_unknown_vehicle_is_404(viewer):
    assert (await viewer.post("/api/vehicles/999/checkin")).status_code == 404


async def test_checked_in_viewer_may_patch_that_vehicles_position(viewer):
    await viewer.post("/api/vehicles/1/checkin")
    response = await viewer.patch("/api/vehicles/1/position", json={"lat": 53.56, "lng": 9.98})
    assert response.status_code == 200


async def test_checked_in_viewer_may_not_patch_a_different_vehicle(viewer):
    await viewer.post("/api/vehicles/1/checkin")
    response = await viewer.patch("/api/vehicles/2/position", json={"lat": 53.56, "lng": 9.98})
    assert response.status_code == 403


async def test_geolocation_outside_hamburg_is_still_rejected_for_a_checked_in_user(viewer):
    await viewer.post("/api/vehicles/1/checkin")
    response = await viewer.patch("/api/vehicles/1/position", json={"lat": 52.52, "lng": 13.40})
    assert response.status_code == 400


async def test_admin_may_patch_without_checking_in(admin):
    response = await admin.patch("/api/vehicles/1/position", json={"lat": 53.56, "lng": 9.98})
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# Geocoding
# --------------------------------------------------------------------------- #
def test_parse_nominatim_reads_lat_lon_and_display_name():
    result = parse_nominatim(
        [{"lat": "53.550341", "lon": "9.992196", "display_name": "Hamburg Hauptbahnhof"}]
    )
    assert result is not None
    assert (result.lat, result.lng) == (53.55034, 9.9922)
    assert result.display_name == "Hamburg Hauptbahnhof"


def test_parse_nominatim_accepts_numeric_values():
    result = parse_nominatim([{"lat": 53.5, "lon": 9.9, "display_name": "Test"}])
    assert result is not None
    assert (result.lat, result.lng) == (53.5, 9.9)


@pytest.mark.parametrize(
    "payload",
    [[], {}, [{"lat": "53.5"}], [{"lat": "keine-zahl", "lon": "9.9", "display_name": "x"}], "junk"],
)
def test_parse_nominatim_returns_none_for_unusable_payloads(payload):
    assert parse_nominatim(payload) is None


async def test_geocode_requires_authentication(anon):
    assert (await anon.get("/api/geocode", params={"address": "Rathaus"})).status_code == 401


def _stub_nominatim(monkeypatch, *, response=None, raises=None):
    async def fake(settings, address):
        if raises is not None:
            raise raises
        return response

    monkeypatch.setattr(misc, "fetch_nominatim", fake)


async def test_geocode_returns_a_hit(admin, monkeypatch):
    _stub_nominatim(
        monkeypatch,
        response=httpx.Response(200, json=[{"lat": "53.550341", "lon": "9.992196", "display_name": "Hbf"}]),
    )
    response = await admin.get("/api/geocode", params={"address": "Hauptbahnhof"})
    assert response.status_code == 200
    assert response.json() == {"lat": 53.55034, "lng": 9.9922, "displayName": "Hbf"}


async def test_geocode_query_stays_inside_the_hamburg_viewbox(admin, monkeypatch):
    """Nominatim is asked with bounded=1, so no hit outside Hamburg comes back."""
    captured = {}

    async def fake_send(self, request, **kwargs):
        captured["url"] = str(request.url)
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(
            200,
            json=[{"lat": "53.55", "lon": "9.99", "display_name": "Hbf"}],
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_send)
    await misc.fetch_nominatim(get_settings(), "Hauptbahnhof")
    assert "bounded=1" in captured["url"]
    assert "viewbox=9.6%2C53.3%2C10.4%2C53.8" in captured["url"]
    assert "ffw-trainingskarte" in captured["ua"]


async def test_geocode_with_no_hit_is_404(admin, monkeypatch):
    _stub_nominatim(monkeypatch, response=httpx.Response(200, json=[]))
    response = await admin.get("/api/geocode", params={"address": "Gibtsnicht"})
    assert response.status_code == 404


async def test_geocode_passes_through_a_rate_limit(admin, monkeypatch):
    _stub_nominatim(monkeypatch, response=httpx.Response(429, text=""))
    response = await admin.get("/api/geocode", params={"address": "Rathaus"})
    assert response.status_code == 429


async def test_geocode_timeout_becomes_504(admin, monkeypatch):
    _stub_nominatim(monkeypatch, raises=httpx.ConnectTimeout("timeout"))
    response = await admin.get("/api/geocode", params={"address": "Rathaus"})
    assert response.status_code == 504


# --------------------------------------------------------------------------- #
# Version
# --------------------------------------------------------------------------- #
async def test_version_endpoint_reports_a_version(admin):
    response = await admin.get("/api/version")
    assert response.status_code == 200
    assert response.json()["version"]
