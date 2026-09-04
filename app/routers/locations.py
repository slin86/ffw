"""REST API for stations and incidents.

Kept as two routers in one module: they are two endpoints over the same table,
and M12/M13 already established that they stay separate at the HTTP level.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Incident, Station
from app.schemas import ActiveRequest, IncidentOut, LocationRequest, StationOut
from app.security import AdminUser, AuthUser

DB = Annotated[AsyncSession, Depends(get_db)]

stations = APIRouter(prefix="/api/stations", tags=["stations"])
incidents = APIRouter(prefix="/api/incidents", tags=["incidents"])


# --------------------------------------------------------------------------- #
# Stations
# --------------------------------------------------------------------------- #
async def _get_station(db: AsyncSession, station_id: int) -> Station:
    station = await db.get(Station, station_id)
    if station is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Station not found: {station_id}")
    return station


@stations.get("", response_model=list[StationOut])
async def list_stations(db: DB, _: AuthUser) -> list[Station]:
    return list(await db.scalars(select(Station).order_by(Station.name)))


@stations.get("/{station_id}", response_model=StationOut)
async def get_station(station_id: int, db: DB, _: AuthUser) -> Station:
    return await _get_station(db, station_id)


@stations.post("", response_model=StationOut, status_code=status.HTTP_201_CREATED)
async def create_station(payload: LocationRequest, db: DB, _: AdminUser) -> Station:
    station = Station(name=payload.name, lat=payload.lat, lng=payload.lng, description=payload.description)
    db.add(station)
    await db.commit()
    await db.refresh(station)
    return station


@stations.put("/{station_id}", response_model=StationOut)
async def update_station(station_id: int, payload: LocationRequest, db: DB, _: AdminUser) -> Station:
    station = await _get_station(db, station_id)
    station.name = payload.name
    station.lat = payload.lat
    station.lng = payload.lng
    station.description = payload.description
    await db.commit()
    await db.refresh(station)
    return station


@stations.delete("/{station_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_station(station_id: int, db: DB, _: AdminUser) -> None:
    station = await db.get(Station, station_id)
    if station is None:
        return
    for vehicle in station.vehicles:
        vehicle.location = None
    await db.delete(station)
    await db.commit()


# --------------------------------------------------------------------------- #
# Incidents
# --------------------------------------------------------------------------- #
async def _get_incident(db: AsyncSession, incident_id: int) -> Incident:
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Incident not found: {incident_id}")
    return incident


@incidents.get("", response_model=list[IncidentOut])
async def list_incidents(db: DB, _: AuthUser, all: bool = False) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.name)
    if not all:
        stmt = stmt.where(Incident.active.is_(True))
    return list(await db.scalars(stmt))


@incidents.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: int, db: DB, _: AuthUser) -> Incident:
    return await _get_incident(db, incident_id)


@incidents.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def create_incident(payload: LocationRequest, db: DB, _: AdminUser) -> Incident:
    incident = Incident(
        name=payload.name,
        lat=payload.lat,
        lng=payload.lng,
        description=payload.description,
        active=True if payload.active is None else payload.active,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)
    return incident


@incidents.put("/{incident_id}", response_model=IncidentOut)
async def update_incident(incident_id: int, payload: LocationRequest, db: DB, _: AdminUser) -> Incident:
    incident = await _get_incident(db, incident_id)
    incident.name = payload.name
    incident.lat = payload.lat
    incident.lng = payload.lng
    incident.description = payload.description
    if payload.active is not None:
        incident.active = payload.active
    await db.commit()
    await db.refresh(incident)
    return incident


@incidents.patch("/{incident_id}/active")
async def set_incident_active(
    incident_id: int, payload: ActiveRequest, db: DB, _: AdminUser
) -> dict[str, object]:
    incident = await _get_incident(db, incident_id)
    incident.active = payload.active
    await db.commit()
    return {"id": incident.id, "active": incident.active}


@incidents.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incident(incident_id: int, db: DB, _: AdminUser) -> None:
    incident = await db.get(Incident, incident_id)
    if incident is None:
        return
    for vehicle in incident.vehicles:
        vehicle.location = None
    await db.delete(incident)
    await db.commit()
