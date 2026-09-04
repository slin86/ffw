"""SQLAlchemy 2.0 models.

`Location` uses single-table inheritance with a `location_type` discriminator,
the direct equivalent of the JPA `@Inheritance(SINGLE_TABLE)` mapping. `Station`
carries no `active` flag (a station is always active); only `Incident` maps it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# BIGSERIAL on Postgres, plain INTEGER on SQLite (used by the test suite).
PK = BigInteger().with_variant(Integer, "sqlite")
FK = BigInteger().with_variant(Integer, "sqlite")

STATUS_LABELS: dict[int, str] = {
    1: "Frei über Funk",
    2: "Frei auf Wache",
    3: "Einsatz übernommen",
    4: "Am Einsatzort",
    6: "Außer Dienst",
}

VALID_STATUSES = frozenset(STATUS_LABELS)

VEHICLE_TYPES = ("HLF", "DLK", "TLF", "MTW", "RW", "ELW")
ROLES = ("ADMIN", "VIEWER")

# Hamburg bounding box, identical to the CHECK constraints on `location`.
MIN_LAT, MAX_LAT = 53.3, 53.8
MIN_LNG, MAX_LNG = 9.6, 10.4

DEFAULT_LAT, DEFAULT_LNG = 53.5511, 9.9937


def utcnow() -> datetime:
    return datetime.now(UTC)


def in_hamburg(lat: float, lng: float) -> bool:
    return MIN_LAT <= lat <= MAX_LAT and MIN_LNG <= lng <= MAX_LNG


class Base(DeclarativeBase):
    pass


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"


class Location(Base):
    """Abstract base for Station and Incident (single table `location`)."""

    __tablename__ = "location"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    location_type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    lat: Mapped[float] = mapped_column(nullable=False)
    lng: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    vehicles: Mapped[list[Vehicle]] = relationship(
        back_populates="location", lazy="selectin", order_by="Vehicle.callsign"
    )

    __mapper_args__ = {
        "polymorphic_on": location_type,
        # No identity on the base: `Location` is never instantiated directly.
        "with_polymorphic": "*",
    }


class Station(Location):
    __mapper_args__ = {"polymorphic_identity": "STATION"}


class Incident(Location):
    # Lives on the shared `location` table, hence nullable.
    active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)

    __mapper_args__ = {"polymorphic_identity": "INCIDENT"}


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    callsign: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    lat: Mapped[float] = mapped_column(nullable=False)
    lng: Mapped[float] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    location_id: Mapped[int | None] = mapped_column(FK, ForeignKey("location.id"), nullable=True)

    location: Mapped[Location | None] = relationship(back_populates="vehicles", lazy="selectin")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, str(self.status))


class VehicleCheckin(Base):
    __tablename__ = "vehicle_checkin"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(FK, ForeignKey("vehicle.id"), nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    vehicle: Mapped[Vehicle] = relationship(lazy="selectin")
