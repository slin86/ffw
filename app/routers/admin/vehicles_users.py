"""Admin UI for vehicles and users (/admin/vehicles, /admin/users)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db import get_db
from app.models import (
    DEFAULT_LAT,
    DEFAULT_LNG,
    ROLES,
    VEHICLE_TYPES,
    AppUser,
    Vehicle,
    VehicleCheckin,
    utcnow,
)
from app.security import AdminUser, hash_password
from app.templating import flash, render

DB = Annotated[AsyncSession, Depends(get_db)]

vehicles = APIRouter(prefix="/admin/vehicles", tags=["admin"])
users = APIRouter(prefix="/admin/users", tags=["admin"])


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


# --------------------------------------------------------------------------- #
# Vehicles
# --------------------------------------------------------------------------- #
@vehicles.get("")
async def list_vehicles(request: Request, db: DB, _: AdminUser) -> Response:
    result = await db.scalars(select(Vehicle).order_by(Vehicle.callsign))
    return render(
        request,
        "admin/vehicles.html",
        {
            "vehicles": list(result),
            "vehicle_types": VEHICLE_TYPES,
            "current_page": "vehicles",
        },
    )


@vehicles.post("/create")
async def create_vehicle(
    request: Request,
    db: DB,
    _: AdminUser,
    callsign: Annotated[str, Form()],
    type: Annotated[str, Form()],
    status_: Annotated[int, Form(alias="status")],
) -> Response:
    exists = await db.scalar(select(Vehicle).where(Vehicle.callsign == callsign))
    if exists is not None:
        flash(request, f"Funkrufname '{callsign}' existiert bereits", error=True)
        return _redirect("/admin/vehicles")

    db.add(
        Vehicle(
            callsign=callsign,
            type=type,
            status=status_,
            lat=DEFAULT_LAT,
            lng=DEFAULT_LNG,
            updated_at=utcnow(),
        )
    )
    await db.commit()
    flash(request, f"Fahrzeug '{callsign}' angelegt")
    return _redirect("/admin/vehicles")


@vehicles.get("/{vehicle_id}/edit")
async def edit_vehicle(vehicle_id: int, request: Request, db: DB, _: AdminUser) -> Response:
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Fahrzeug nicht gefunden: {vehicle_id}")
    return render(
        request,
        "admin/vehicle-edit.html",
        {"vehicle": vehicle, "vehicle_types": VEHICLE_TYPES, "current_page": "vehicles"},
    )


@vehicles.post("/{vehicle_id}")
async def update_vehicle(
    vehicle_id: int,
    request: Request,
    db: DB,
    _: AdminUser,
    callsign: Annotated[str, Form()],
    type: Annotated[str, Form()],
    status_: Annotated[int, Form(alias="status")],
    lat: Annotated[float, Form()],
    lng: Annotated[float, Form()],
) -> Response:
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Fahrzeug nicht gefunden: {vehicle_id}")

    existing = await db.scalar(select(Vehicle).where(Vehicle.callsign == callsign))
    if existing is not None and existing.id != vehicle_id:
        flash(request, f"Funkrufname '{callsign}' existiert bereits", error=True)
        return _redirect(f"/admin/vehicles/{vehicle_id}/edit")

    vehicle.callsign = callsign
    vehicle.type = type
    vehicle.status = status_
    vehicle.lat = lat
    vehicle.lng = lng
    vehicle.updated_at = utcnow()
    await db.commit()

    flash(request, f"Fahrzeug '{callsign}' aktualisiert")
    return _redirect("/admin/vehicles")


@vehicles.post("/{vehicle_id}/delete")
async def delete_vehicle(vehicle_id: int, request: Request, db: DB, _: AdminUser) -> Response:
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Fahrzeug nicht gefunden: {vehicle_id}")

    callsign = vehicle.callsign
    await db.execute(delete(VehicleCheckin).where(VehicleCheckin.vehicle_id == vehicle_id))
    await db.delete(vehicle)
    await db.commit()

    flash(request, f"Fahrzeug '{callsign}' gelöscht")
    return _redirect("/admin/vehicles")


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
@users.get("")
async def list_users(request: Request, db: DB, admin: AdminUser) -> Response:
    result = await db.scalars(select(AppUser).order_by(AppUser.username))
    return render(
        request,
        "admin/users.html",
        {
            "users": list(result),
            "roles": ROLES,
            "current_username": admin.username,
            "current_page": "users",
        },
    )


@users.post("/create")
async def create_user(
    request: Request,
    db: DB,
    _: AdminUser,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[str, Form()],
) -> Response:
    exists = await db.scalar(select(AppUser).where(AppUser.username == username))
    if exists is not None:
        flash(request, f"Benutzername '{username}' existiert bereits", error=True)
        return _redirect("/admin/users")

    db.add(
        AppUser(
            username=username,
            password_hash=hash_password(password),
            role=role if role in ROLES else "VIEWER",
            enabled=True,
        )
    )
    await db.commit()
    flash(request, f"Nutzer '{username}' angelegt")
    return _redirect("/admin/users")


@users.post("/{user_id}/toggle")
async def toggle_user(user_id: int, request: Request, db: DB, admin: AdminUser) -> Response:
    user = await db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nutzer nicht gefunden: {user_id}")

    if user.username == admin.username:
        flash(request, "Sie können Ihr eigenes Konto nicht deaktivieren", error=True)
        return _redirect("/admin/users")

    user.enabled = not user.enabled
    await db.commit()
    flash(request, f"Nutzer '{user.username}' {'aktiviert' if user.enabled else 'deaktiviert'}")
    return _redirect("/admin/users")


@users.post("/{user_id}/delete")
async def delete_user(user_id: int, request: Request, db: DB, admin: AdminUser) -> Response:
    user = await db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nutzer nicht gefunden: {user_id}")

    if user.username == admin.username:
        flash(request, "Sie können Ihr eigenes Konto nicht löschen", error=True)
        return _redirect("/admin/users")

    username = user.username
    await db.execute(delete(VehicleCheckin).where(VehicleCheckin.username == username))
    await db.delete(user)
    await db.commit()
    flash(request, f"Nutzer '{username}' gelöscht")
    return _redirect("/admin/users")
