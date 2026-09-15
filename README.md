# FF-Trainingskarte (Python)

Training tool for the volunteer fire department: vehicle positions and status on
an interactive map of Hamburg, used to train the Emergency Operations Director
programme (ELA/EDV) with realistic radio traffic.

Port of [`slin86/ffw-trainingsmap`](https://github.com/slin86/ffw-trainingsmap)
from Spring Boot 4.1 / Java 21. Same database schema, same HTTP API, same UI.

## Stack

| Layer | Java version | Here |
|---|---|---|
| Web framework | Spring MVC | FastAPI (async) |
| ORM | JPA / Hibernate | SQLAlchemy 2.0 async + asyncpg |
| Migrations | Flyway | Alembic |
| Templates | Thymeleaf | Jinja2 |
| Sessions | Spring Session Data Redis | own Redis store, cookie `SESSION` |
| Auth | Spring Security 7 | bcrypt + dependency-based roles + CSRF middleware |
| JSON | Jackson | Pydantic v2 |
| Lint/format | Spotless / Checkstyle | ruff (incl. `C901` complexity gate) |
| Map | Leaflet + OpenStreetMap | unchanged |
| Geocoding | Nominatim | unchanged |

Deliberate non-goals carried over: no realtime push (SSE/WebSockets) — the 10s
polling is an architectural decision. No MongoDB, no Qdrant.

## Local development

```bash
docker compose up -d                 # postgres:16 + redis:7
uv sync --extra dev
cp .env.example .env                 # APP_PROFILE=dev enables the seeder
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload --port 8080
```

App: http://localhost:8080 · `admin` / `admin` (ADMIN), `viewer` / `viewer` (VIEWER).
Seeds four Hamburg vehicles: HLF 20/1, DLK 12/1, TLF 3/1, MTW 1/1.

```bash
uv run pytest -q          # 96 tests
uv run ruff check .
uv run ruff format --check .
```

## Migrating an existing deployment

The schema is unchanged, so **do not run `alembic upgrade head` against the
database Flyway already migrated**. Record the current state instead:

```bash
alembic stamp 0001_initial
```

Everything else carries over:

* **Password hashes** verify as they are. The bcrypt `$2a$` hashes Spring
  Security wrote are validated by `tests/test_auth.py` against the jBCrypt test
  vectors, so no password reset is needed.
* **`DB_URL`** may stay in its `jdbc:postgresql://` form; `app/config.py`
  translates it. The ConfigMap and the InfisicalSecret at `/feuerwehr` need no
  edit.
* **Session cookie** is still named `SESSION`, so nothing about the Traefik or
  ingress setup changes. Existing sessions are invalidated once (different
  serialisation in Redis) — users log in again, that is all.
* **Health probes** move from `/actuator/health` to `/api/version`, which is
  public for the same reason it was in the Spring version.
* The old `flyway_schema_history` table can stay; nothing reads it any more.

## API

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/api/vehicles` | authenticated | all vehicles |
| POST | `/api/vehicles` | ADMIN | create |
| PUT | `/api/vehicles/{id}` | ADMIN | update base data |
| PUT | `/api/vehicles/{id}/position` | ADMIN | set position (Hamburg bounds) |
| PATCH | `/api/vehicles/{id}/position` | ADMIN or checked-in user | geolocation update |
| PUT | `/api/vehicles/{id}/status` | authenticated | status 1, 2, 3, 4, 6 |
| PATCH | `/api/vehicles/{id}/location` | ADMIN | assign / clear (`null` = unterwegs) |
| DELETE | `/api/vehicles/{id}` | ADMIN | delete |
| GET | `/api/stations` | authenticated | stations incl. their vehicles |
| GET/POST/PUT/DELETE | `/api/stations/{id}` | ADMIN for writes | CRUD |
| GET | `/api/incidents?all=true\|false` | authenticated | all or active only |
| PATCH | `/api/incidents/{id}/active` | ADMIN | toggle |
| POST | `/api/vehicles/{id}/checkin` | authenticated | check in (replaces previous) |
| POST | `/api/vehicles/{id}/checkout` | authenticated | idempotent |
| GET | `/api/checkin/me` | authenticated | current vehicle or 204 |
| GET | `/api/geocode?address=` | authenticated | Nominatim proxy, Hamburg-bounded |
| GET | `/api/version` | public | app version |

OpenAPI at `/api/docs` — new, and free from FastAPI.

## Status codes (ELA)

| Code | Meaning | Colour |
|---|---|---|
| 1 | Frei über Funk | green |
| 2 | Frei auf Wache | green |
| 3 | Einsatz übernommen | red |
| 4 | Am Einsatzort | red |
| 6 | Außer Dienst | grey |

## Bugs found in the Java version and fixed here

Found while reading the source for the port. Each has a test or is visible in
the browser:

1. **`isAdmin()` in `map.js` checked for the `_csrf` meta tag**, which every
   authenticated user has. VIEWERs got draggable markers and the vehicle
   assignment dropdown and only learned it was not allowed from a 403. Now read
   from an `_is_admin` meta tag rendered server-side
   (`test_map_page_marks_admins_and_viewers_differently`).
2. **`location_type` was never in the JSON.** Jackson serialised
   `getLocationType()` as `locationType`, while `map.js` reads
   `location.location_type`. The comparison was always `undefined`, so every
   location — fire stations included — was labelled "Einsatzort" in its popup.
   The Pydantic schema emits `location_type` explicitly
   (`test_station_json_carries_location_type`).
3. **Drag-and-drop (M10) could never have worked.** `L.circleMarker` ignores
   `draggable` — it is an option of `L.Marker`, not of `Path`. Now `L.marker`
   with a `divIcon` that keeps the coloured-dot look.
4. **`updateVehiclePosition()` returned nothing**, but the `dragend` handler
   chains `.catch()` onto it, so a failed move threw a `TypeError` instead of
   rolling the marker back.
5. `currentPage` is read by `_sidebar.html` but was never set by any controller,
   so the admin nav never highlighted the active section
   (`test_active_nav_entry_is_marked`).
6. `stations.html` and `incidents.html` used `<head>` instead of `<thead>`
   inside the table.
7. Deleting a station or incident left `vehicle.location_id` pointing at a gone
   row. Vehicles now fall back to "unterwegs"
   (`test_deleting_a_station_sets_its_vehicles_to_unterwegs`).
8. `map.invalidateSize()` was missing, so popups were clipped after a resize or
   orientation change. This was still on the open list.

Also simplified: the ~130-line hand-written JSON scanner in `GeocodeController`
became `response.json()` plus a few guards, and the ~100-line position-picker
script duplicated in `station-form.html` and `incident-form.html` is now one
`location-form.js`.

## Layout

```
app/
├── main.py            # app factory, middleware order, exception handlers
├── config.py          # pydantic-settings, JDBC URL translation
├── db.py              # async engine, per-request session
├── models.py          # SQLAlchemy: AppUser, Vehicle, Location STI, VehicleCheckin
├── schemas.py         # Pydantic DTOs, JSON field-name contract
├── security.py        # bcrypt, CSRF middleware, role dependencies
├── sessions.py        # Redis session store + middleware
├── templating.py      # Jinja env, flash messages, CSRF helpers
├── seed.py            # dev seeder (APP_PROFILE=dev)
├── routers/           # vehicles, locations, misc (checkin/geocode/version), web
│   └── admin/         # admin UI routes
├── templates/         # Jinja2
└── static/            # css/admin.css, js/map.js, js/location-form.js
migrations/            # Alembic
tests/                 # 96 tests, SQLite + in-memory sessions
deploy/                # k8s manifests
```

## Type checking

`mypy` runs in `strict` mode over `app/` and `tests/` and is part of the CI gate:

```bash
uv run mypy app tests
```

Tests are exempt from `disallow_untyped_defs` only — every other check applies.
`Mapped[...]` in `app/models.py` is what makes the SQLAlchemy models checkable:
it tells mypy that `vehicle.callsign` is a `str` on the instance while
`Vehicle.callsign` is a query expression on the class.
