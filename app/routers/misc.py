"""Check-in, geocoding proxy and version endpoint."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings, read_version
from app.db import get_db
from app.models import Vehicle, VehicleCheckin, utcnow
from app.schemas import CheckinResponse, GeocodeResponse, VersionResponse
from app.security import AuthUser

DB = Annotated[AsyncSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]

# Read once at import time, like VersionController's @PostConstruct.
APP_VERSION = read_version()

checkin = APIRouter(prefix="/api", tags=["checkin"])
geocode = APIRouter(prefix="/api", tags=["geocode"])
version = APIRouter(prefix="/api", tags=["version"])


# --------------------------------------------------------------------------- #
# Check-in
# --------------------------------------------------------------------------- #
@checkin.post(
    "/vehicles/{vehicle_id}/checkin",
    response_model=CheckinResponse,
    status_code=status.HTTP_201_CREATED,
)
async def check_in(vehicle_id: int, db: DB, user: AuthUser) -> CheckinResponse:
    """A user is checked in on at most one vehicle; a new check-in replaces the old."""
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Vehicle not found: {vehicle_id}")

    existing = await db.scalar(select(VehicleCheckin).where(VehicleCheckin.username == user.username))
    if existing is not None:
        await db.delete(existing)
        await db.flush()

    db.add(VehicleCheckin(vehicle_id=vehicle_id, username=user.username, checked_in_at=utcnow()))
    await db.commit()
    return CheckinResponse(vehicle_id=vehicle_id)


@checkin.post("/vehicles/{vehicle_id}/checkout", status_code=status.HTTP_204_NO_CONTENT)
async def check_out(vehicle_id: int, db: DB, user: AuthUser) -> None:
    """Idempotent: checking out of a vehicle you are not on is a no-op."""
    existing = await db.scalar(select(VehicleCheckin).where(VehicleCheckin.username == user.username))
    if existing is not None and existing.vehicle_id == vehicle_id:
        await db.delete(existing)
        await db.commit()


@checkin.get("/checkin/me")
async def my_checkin(db: DB, user: AuthUser) -> Response:
    existing = await db.scalar(select(VehicleCheckin).where(VehicleCheckin.username == user.username))
    if existing is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=CheckinResponse(vehicle_id=existing.vehicle_id).model_dump_json(by_alias=True),
        media_type="application/json",
    )


# --------------------------------------------------------------------------- #
# Geocoding
# --------------------------------------------------------------------------- #
def parse_nominatim(payload: object) -> GeocodeResponse | None:
    """Pick the first usable hit out of a Nominatim response.

    The Java version hand-rolled a brace-counting JSON scanner here. A real
    parser plus a couple of guards does the same job in a fifth of the lines and
    without the failure modes of the scanner (escaped braces inside strings).
    """
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        return None

    first = payload[0]
    if not isinstance(first, dict):
        return None
    raw_lng = first.get("lon", first.get("lng"))
    if raw_lng is None:
        return None
    try:
        lat = float(first["lat"])
        lng = float(raw_lng)
        display_name = str(first["display_name"])
    except (KeyError, TypeError, ValueError):
        return None

    return GeocodeResponse(lat=round(lat, 5), lng=round(lng, 5), display_name=display_name)


async def fetch_nominatim(settings: Settings, address: str) -> httpx.Response:
    """Isolated so tests can stub the upstream call.

    Patching httpx.AsyncClient globally would also hit the test client itself.
    """
    params = {
        "format": "json",
        "limit": "1",
        "viewbox": "9.6,53.3,10.4,53.8",  # Hamburg only
        "bounded": "1",
        "q": address,
    }
    headers = {"User-Agent": settings.nominatim_user_agent}
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.get(settings.nominatim_url, params=params, headers=headers)


@geocode.get("/geocode", response_model=GeocodeResponse)
async def geocode_address(
    settings: Config,
    _: AuthUser,
    address: Annotated[str, Query(min_length=1)],
) -> GeocodeResponse:
    if not address.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Address parameter is required")

    try:
        response = await fetch_nominatim(settings, address)
    except httpx.TimeoutException as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "Geocoding service timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Geocoding service unavailable") from exc

    if response.status_code == 429:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    if response.status_code != 200:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Geocoding service unavailable")

    try:
        result = parse_nominatim(response.json())
    except ValueError:
        result = None

    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No address found for: {address}")
    return result


# --------------------------------------------------------------------------- #
# Version
# --------------------------------------------------------------------------- #
@version.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    """Public on purpose — the map header reads it before anything else loads."""
    return VersionResponse(version=APP_VERSION)
