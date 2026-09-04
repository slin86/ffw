"""/api/vehicles — status rules, Hamburg bounds, role enforcement."""

from __future__ import annotations

import pytest


async def test_list_returns_vehicles_for_any_authenticated_user(viewer):
    response = await viewer.get("/api/vehicles")
    assert response.status_code == 200
    callsigns = [v["callsign"] for v in response.json()]
    assert callsigns == ["DLK 12/1", "HLF 20/1"]


async def test_vehicle_json_uses_the_field_names_map_js_reads(admin):
    vehicle = (await admin.get("/api/vehicles")).json()[0]
    assert "updatedAt" in vehicle  # camelCase, as Jackson emitted it
    assert {"id", "callsign", "type", "status", "lat", "lng", "location"} <= vehicle.keys()


async def test_create_requires_admin(viewer):
    response = await viewer.post("/api/vehicles", json={"callsign": "RW 1/1", "type": "RW", "status": 2})
    assert response.status_code == 403


async def test_create_places_the_vehicle_at_the_default_position(admin):
    response = await admin.post("/api/vehicles", json={"callsign": "RW 1/1", "type": "RW", "status": 2})
    assert response.status_code == 201
    body = response.json()
    assert (body["lat"], body["lng"]) == (53.5511, 9.9937)


async def test_duplicate_callsign_is_rejected(admin):
    response = await admin.post("/api/vehicles", json={"callsign": "HLF 20/1", "type": "HLF", "status": 2})
    assert response.status_code == 400
    assert "bereits vorhanden" in response.json()["error"]


async def test_update_may_keep_its_own_callsign(admin):
    response = await admin.put("/api/vehicles/1", json={"callsign": "HLF 20/1", "type": "HLF", "status": 3})
    assert response.status_code == 200
    assert response.json()["status"] == 3


async def test_update_rejects_a_callsign_owned_by_another_vehicle(admin):
    response = await admin.put("/api/vehicles/1", json={"callsign": "DLK 12/1", "type": "HLF", "status": 2})
    assert response.status_code == 400


@pytest.mark.parametrize("status_code", [1, 2, 3, 4, 6])
async def test_viewer_may_set_every_valid_status(viewer, status_code):
    """The core of the training tool: VIEWER changes status, ADMIN is not needed."""
    response = await viewer.put("/api/vehicles/1/status", json={"status": status_code})
    assert response.status_code == 200
    assert response.json()["status"] == status_code


@pytest.mark.parametrize("status_code", [0, 5, 7, -1, 99])
async def test_invalid_status_is_rejected(admin, status_code):
    response = await admin.put("/api/vehicles/1/status", json={"status": status_code})
    assert response.status_code == 400
    assert "1, 2, 3, 4, 6" in response.json()["error"]


async def test_status_change_on_unknown_vehicle_is_404(admin):
    response = await admin.put("/api/vehicles/999/status", json={"status": 1})
    assert response.status_code == 404


@pytest.mark.parametrize(
    "lat,lng",
    [(53.2, 9.99), (53.9, 9.99), (53.55, 9.5), (53.55, 10.5), (52.52, 13.40)],
)
async def test_position_outside_hamburg_is_rejected(admin, lat, lng):
    response = await admin.put("/api/vehicles/1/position", json={"lat": lat, "lng": lng})
    assert response.status_code == 400
    assert "Hamburger Stadtgebiet" in response.json()["error"]


@pytest.mark.parametrize("lat,lng", [(53.3, 9.6), (53.8, 10.4), (53.55, 9.99)])
async def test_position_inside_hamburg_including_the_edges_is_accepted(admin, lat, lng):
    response = await admin.put("/api/vehicles/1/position", json={"lat": lat, "lng": lng})
    assert response.status_code == 200


async def test_viewer_may_not_move_a_vehicle_via_put(viewer):
    response = await viewer.put("/api/vehicles/1/position", json={"lat": 53.55, "lng": 9.99})
    assert response.status_code == 403


async def test_admin_assigns_and_clears_a_location(admin):
    assigned = await admin.patch("/api/vehicles/1/location", json={"locationId": 1})
    assert assigned.status_code == 200
    assert assigned.json()["location"]["id"] == 1
    assert assigned.json()["location"]["location_type"] == "STATION"

    cleared = await admin.patch("/api/vehicles/1/location", json={"locationId": None})
    assert cleared.status_code == 200
    assert cleared.json()["location"] is None  # "unterwegs"


async def test_assigning_an_unknown_location_is_rejected(admin):
    response = await admin.patch("/api/vehicles/1/location", json={"locationId": 999})
    assert response.status_code == 400


async def test_viewer_may_not_assign_a_location(viewer):
    response = await viewer.patch("/api/vehicles/1/location", json={"locationId": 1})
    assert response.status_code == 403


async def test_delete_requires_admin(viewer, admin):
    assert (await viewer.delete("/api/vehicles/1")).status_code == 403
    assert (await admin.delete("/api/vehicles/1")).status_code == 204
    assert len((await admin.get("/api/vehicles")).json()) == 1
