"""Dev seed data — the DataSeeder @Profile("dev") equivalent.

Only runs when APP_PROFILE=dev, and only inserts what is missing.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import AppUser, Vehicle
from app.security import hash_password

log = logging.getLogger(__name__)

SEED_VEHICLES = [
    ("HLF 20/1", "HLF 20", 2, 53.587, 10.044),  # Wandsbek
    ("DLK 12/1", "DLK 23/12", 2, 53.594, 9.990),  # Altona
    ("TLF 3/1", "TLF 3000", 2, 53.552, 9.935),  # Hamburg-Mitte
    ("MTW 1/1", "MTW", 1, 53.460, 9.983),  # Harburg
]


async def seed_dev_data() -> None:
    async with get_sessionmaker()() as db:
        log.info("Seede Daten für die Entwicklungsumgebung...")

        if await db.scalar(select(AppUser).where(AppUser.username == "admin")) is None:
            db.add(
                AppUser(
                    username="admin",
                    password_hash=hash_password("admin"),
                    role="ADMIN",
                    enabled=True,
                )
            )
            log.info("Admin-Nutzer angelegt.")

        if await db.scalar(select(AppUser).where(AppUser.username == "viewer")) is None:
            db.add(
                AppUser(
                    username="viewer",
                    password_hash=hash_password("viewer"),
                    role="VIEWER",
                    enabled=True,
                )
            )
            log.info("Viewer-Nutzer angelegt.")

        if (await db.scalar(select(func.count(Vehicle.id)))) == 0:
            for callsign, type_, status, lat, lng in SEED_VEHICLES:
                db.add(Vehicle(callsign=callsign, type=type_, status=status, lat=lat, lng=lng))
            log.info("4 Fahrzeuge für Hamburger Feuerwachen angelegt.")

        await db.commit()
