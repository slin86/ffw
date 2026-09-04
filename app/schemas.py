"""Pydantic v2 schemas — the DTO records plus the Jackson serialisation rules.

The JSON field names are chosen to match what map.js already reads:
`updatedAt` (camelCase) and `location_type` (snake_case). The Java version fed
entities straight to Jackson, which emitted `locationType` from
`getLocationType()` — so `location.location_type === 'STATION'` in map.js was
always undefined and every location rendered as "Einsatzort" in its popup. The
explicit schema here fixes that by construction.

Nesting is broken deliberately: a vehicle's `location` has no vehicles, and a
location's `vehicles` have no location. Jackson needed
@JsonManagedReference/@JsonBackReference for the same reason.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class VehicleRequest(BaseModel):
    callsign: str
    type: str
    status: int


class PositionRequest(BaseModel):
    lat: float
    lng: float


class StatusChangeRequest(BaseModel):
    status: int


class LocationIdRequest(BaseModel):
    location_id: int | None = Field(default=None, alias="locationId")

    model_config = ConfigDict(populate_by_name=True)


class LocationRequest(BaseModel):
    name: str
    lat: float
    lng: float
    description: str | None = None
    active: bool | None = None
    type: str | None = None


class ActiveRequest(BaseModel):
    active: bool


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class LocationRef(ORMModel):
    """A vehicle's location — without its vehicle list."""

    id: int
    name: str
    lat: float
    lng: float
    location_type: str
    description: str | None = None


class VehicleRef(ORMModel):
    """A location's vehicle — without its location."""

    id: int
    callsign: str
    type: str
    status: int


class VehicleOut(ORMModel):
    id: int
    callsign: str
    type: str
    status: int
    lat: float
    lng: float
    updated_at: datetime = Field(serialization_alias="updatedAt")
    location: LocationRef | None = None


class LocationOut(ORMModel):
    id: int
    name: str
    lat: float
    lng: float
    location_type: str
    description: str | None = None
    created_at: datetime = Field(serialization_alias="createdAt")
    vehicles: list[VehicleRef] = []


class StationOut(LocationOut):
    pass


class IncidentOut(LocationOut):
    active: bool | None = None


class GeocodeResponse(BaseModel):
    lat: float
    lng: float
    display_name: str = Field(serialization_alias="displayName")


class VersionResponse(BaseModel):
    version: str


class CheckinResponse(BaseModel):
    vehicle_id: int = Field(serialization_alias="vehicleId")
