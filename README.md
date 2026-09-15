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
