"""Admin UI for stations and incidents (/admin/stations, /admin/incidents)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Incident, Station, Vehicle
from app.security import AdminUser
from app.templating import flash, render

DB = Annotated[AsyncSession, Depends(get_db)]

stations = APIRouter(prefix="/admin/stations", tags=["admin"])
incidents = APIRouter(prefix="/admin/incidents", tags=["admin"])


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


async def _detach_vehicles(db: AsyncSession, location_id: int) -> None:
    """Vehicles at a deleted location go back to 'unterwegs' (location_id NULL)."""
    assigned = await db.scalars(select(Vehicle).where(Vehicle.location_id == location_id))
    for vehicle in assigned:
        vehicle.location = None


# --------------------------------------------------------------------------- #
# Stations
# --------------------------------------------------------------------------- #
@stations.get("")
async def list_stations(request: Request, db: DB, _: AdminUser):
    result = await db.scalars(select(Station).order_by(Station.name))
    return render(
        request,
        "admin/stations.html",
        {"stations": list(result), "current_page": "stations"},
    )


@stations.get("/new")
async def new_station(request: Request, _: AdminUser):
    return render(request, "admin/station-form.html", {"station": None, "current_page": "stations"})


@stations.get("/{station_id}/edit")
async def edit_station(station_id: int, request: Request, db: DB, _: AdminUser):
    station = await db.get(Station, station_id)
    if station is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Ort nicht gefunden: {station_id}")
    return render(request, "admin/station-form.html", {"station": station, "current_page": "stations"})


@stations.post("")
async def create_station(
    request: Request,
    db: DB,
    _: AdminUser,
    name: Annotated[str, Form()],
    lat: Annotated[float, Form()],
    lng: Annotated[float, Form()],
    description: Annotated[str | None, Form()] = None,
):
    db.add(Station(name=name, lat=lat, lng=lng, description=description or None))
    await db.commit()
    flash(request, f"Feuerwache '{name}' angelegt")
    return _redirect("/admin/stations")


@stations.post("/{station_id}")
async def update_station(
    station_id: int,
    request: Request,
    db: DB,
    _: AdminUser,
    name: Annotated[str, Form()],
    lat: Annotated[float, Form()],
    lng: Annotated[float, Form()],
    description: Annotated[str | None, Form()] = None,
):
    station = await db.get(Station, station_id)
    if station is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Ort nicht gefunden: {station_id}")

    station.name = name
    station.lat = lat
    station.lng = lng
    station.description = description or None
    await db.commit()

    flash(request, f"Feuerwache '{name}' aktualisiert")
    return _redirect("/admin/stations")


@stations.post("/{station_id}/delete")
async def delete_station(station_id: int, request: Request, db: DB, _: AdminUser):
    station = await db.get(Station, station_id)
    if station is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Ort nicht gefunden: {station_id}")

    name = station.name
    await _detach_vehicles(db, station_id)
    await db.delete(station)
    await db.commit()

    flash(request, f"Feuerwache '{name}' gelöscht")
    return _redirect("/admin/stations")


# --------------------------------------------------------------------------- #
# Incidents
# --------------------------------------------------------------------------- #
@incidents.get("")
async def list_incidents(request: Request, db: DB, _: AdminUser, all: bool = False):
    stmt = select(Incident).order_by(Incident.name)
    if not all:
        stmt = stmt.where(Incident.active.is_(True))
    result = await db.scalars(stmt)
    return render(
        request,
        "admin/incidents.html",
        {"incidents": list(result), "show_all": all, "current_page": "incidents"},
    )


@incidents.get("/new")
async def new_incident(request: Request, _: AdminUser):
    return render(request, "admin/incident-form.html", {"incident": None, "current_page": "incidents"})


@incidents.get("/{incident_id}/edit")
async def edit_incident(incident_id: int, request: Request, db: DB, _: AdminUser):
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Einsatzort nicht gefunden: {incident_id}")
    return render(request, "admin/incident-form.html", {"incident": incident, "current_page": "incidents"})


@incidents.post("")
async def create_incident(
    request: Request,
    db: DB,
    _: AdminUser,
    name: Annotated[str, Form()],
    lat: Annotated[float, Form()],
    lng: Annotated[float, Form()],
    description: Annotated[str | None, Form()] = None,
):
    db.add(Incident(name=name, lat=lat, lng=lng, description=description or None, active=True))
    await db.commit()
    flash(request, f"Einsatzort '{name}' angelegt")
    return _redirect("/admin/incidents")


@incidents.post("/{incident_id}")
async def update_incident(
    incident_id: int,
    request: Request,
    db: DB,
    _: AdminUser,
    name: Annotated[str, Form()],
    lat: Annotated[float, Form()],
    lng: Annotated[float, Form()],
    description: Annotated[str | None, Form()] = None,
    active: Annotated[bool | None, Form()] = None,
):
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Einsatzort nicht gefunden: {incident_id}")

    incident.name = name
    incident.lat = lat
    incident.lng = lng
    incident.description = description or None
    if active is not None:
        incident.active = active
    await db.commit()

    flash(request, f"Einsatzort '{name}' aktualisiert")
    return _redirect("/admin/incidents")


@incidents.post("/{incident_id}/toggle")
async def toggle_incident(incident_id: int, request: Request, db: DB, _: AdminUser):
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Einsatzort nicht gefunden: {incident_id}")

    incident.active = not incident.active
    await db.commit()

    flash(request, "Einsatzort aktiviert" if incident.active else "Einsatzort deaktiviert")
    return _redirect("/admin/incidents")


@incidents.post("/{incident_id}/delete")
async def delete_incident(incident_id: int, request: Request, db: DB, _: AdminUser):
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Einsatzort nicht gefunden: {incident_id}")

    name = incident.name
    await _detach_vehicles(db, incident_id)
    await db.delete(incident)
    await db.commit()

    flash(request, f"Einsatzort '{name}' gelöscht")
    return _redirect("/admin/incidents")
