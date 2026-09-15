"""REST API for vehicles — /api/vehicles."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import (
    DEFAULT_LAT,
    DEFAULT_LNG,
    VALID_STATUSES,
    Location,
    Vehicle,
    VehicleCheckin,
    in_hamburg,
    utcnow,
)
from app.schemas import (
    LocationIdRequest,
    PositionRequest,
    StatusChangeRequest,
    VehicleOut,
    VehicleRequest,
)
from app.security import AdminUser, AuthUser

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])

DB = Annotated[AsyncSession, Depends(get_db)]

BOUNDS_ERROR = "Koordinaten müssen im Hamburger Stadtgebiet liegen: lat 53.3-53.8, lng 9.6-10.4"


async def _get_vehicle(db: AsyncSession, vehicle_id: int) -> Vehicle:
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Vehicle not found: {vehicle_id}")
    return vehicle


def _require_bounds(lat: float, lng: float) -> None:
    if not in_hamburg(lat, lng):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BOUNDS_ERROR)


async def _assert_callsign_free(db: AsyncSession, callsign: str, exclude_id: int | None = None) -> None:
    stmt = select(Vehicle).where(Vehicle.callsign == callsign)
    existing = await db.scalar(stmt)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Callsign bereits vorhanden: {callsign}")


@router.get("", response_model=list[VehicleOut])
async def list_vehicles(db: DB, _: AuthUser) -> list[Vehicle]:
    result = await db.scalars(select(Vehicle).order_by(Vehicle.callsign))
    return list(result)


@router.post("", response_model=VehicleOut, status_code=status.HTTP_201_CREATED)
async def create_vehicle(payload: VehicleRequest, db: DB, _: AdminUser) -> Vehicle:
    await _assert_callsign_free(db, payload.callsign)
    vehicle = Vehicle(
        callsign=payload.callsign,
        type=payload.type,
        status=payload.status,
        lat=DEFAULT_LAT,
        lng=DEFAULT_LNG,
        updated_at=utcnow(),
    )
    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.put("/{vehicle_id}", response_model=VehicleOut)
async def update_vehicle(vehicle_id: int, payload: VehicleRequest, db: DB, _: AdminUser) -> Vehicle:
    vehicle = await _get_vehicle(db, vehicle_id)
    await _assert_callsign_free(db, payload.callsign, exclude_id=vehicle_id)

    vehicle.callsign = payload.callsign
    vehicle.type = payload.type
    vehicle.status = payload.status
    vehicle.updated_at = utcnow()
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.put("/{vehicle_id}/position", response_model=VehicleOut)
async def put_position(vehicle_id: int, payload: PositionRequest, db: DB, _: AdminUser) -> Vehicle:
    _require_bounds(payload.lat, payload.lng)
    vehicle = await _get_vehicle(db, vehicle_id)
    vehicle.lat, vehicle.lng = payload.lat, payload.lng
    vehicle.updated_at = utcnow()
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.patch("/{vehicle_id}/position", response_model=VehicleOut)
async def patch_position(vehicle_id: int, payload: PositionRequest, db: DB, user: AuthUser) -> Vehicle:
    """ADMIN, or the crew member currently checked in on this vehicle.

    Replaces the SpEL expression
    `@checkInService.isCheckedIn(#id, authentication.name)` with a plain query.
    """
    if not user.is_admin:
        checkin = await db.scalar(select(VehicleCheckin).where(VehicleCheckin.username == user.username))
        if checkin is None or checkin.vehicle_id != vehicle_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Kein Check-in für dieses Fahrzeug")

    _require_bounds(payload.lat, payload.lng)
    vehicle = await _get_vehicle(db, vehicle_id)
    vehicle.lat, vehicle.lng = payload.lat, payload.lng
    vehicle.updated_at = utcnow()
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.put("/{vehicle_id}/status", response_model=VehicleOut)
async def update_status(vehicle_id: int, payload: StatusChangeRequest, db: DB, _: AuthUser) -> Vehicle:
    """Open to VIEWER as well as ADMIN — the whole point of the training tool."""
    if payload.status not in VALID_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Statuswert. Erlaubt: 1, 2, 3, 4, 6")
    vehicle = await _get_vehicle(db, vehicle_id)
    vehicle.status = payload.status
    vehicle.updated_at = utcnow()
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.patch("/{vehicle_id}/location", response_model=VehicleOut)
async def update_location(vehicle_id: int, payload: LocationIdRequest, db: DB, _: AdminUser) -> Vehicle:
    vehicle = await _get_vehicle(db, vehicle_id)

    if payload.location_id is None:
        vehicle.location = None  # "unterwegs"
    else:
        location = await db.get(Location, payload.location_id)
        if location is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Location not found: {payload.location_id}")
        vehicle.location = location

    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(vehicle_id: int, db: DB, _: AdminUser) -> None:
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        return
    # Check-ins reference the vehicle; drop them first so the FK holds.
    await db.execute(delete(VehicleCheckin).where(VehicleCheckin.vehicle_id == vehicle_id))
    await db.delete(vehicle)
    await db.commit()
