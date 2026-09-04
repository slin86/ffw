"""Initial schema.

Reproduces the state Flyway reached after V1-V6, in one revision:

* V1 vehicle + app_user
* V2/V4 scene tables - created and dropped again, so not recreated here
* V3 location (single-table inheritance) + vehicle.location_id
* V5 location.description
* V6 vehicle_checkin

Against a database that Flyway already migrated, do NOT run this. Run
`alembic stamp 0001_initial` instead: the schema is already correct and the
stamp just records that fact in alembic_version. The old flyway_schema_history
table can stay where it is; nothing reads it any more.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("username", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "location",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("location_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lng", sa.Double(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        # Same CHECK constraints as V3 - the Hamburg bounding box is enforced in
        # the database, not only in the request validation.
        sa.CheckConstraint("lat >= 53.3 AND lat <= 53.8", name="chk_location_lat"),
        sa.CheckConstraint("lng >= 9.6 AND lng <= 10.4", name="chk_location_lng"),
    )
    op.create_index("ix_location_type", "location", ["location_type"])

    op.create_table(
        "vehicle",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("callsign", sa.String(), nullable=False, unique=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lng", sa.Double(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # NULL means "unterwegs" - the vehicle uses its own lat/lng.
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=True),
    )

    op.create_table(
        "vehicle_checkin",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("vehicle_id", sa.BigInteger(), sa.ForeignKey("vehicle.id"), nullable=False),
        sa.Column("username", sa.String(), nullable=False, unique=True),
        sa.Column(
            "checked_in_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("vehicle_checkin")
    op.drop_table("vehicle")
    op.drop_index("ix_location_type", table_name="location")
    op.drop_table("location")
    op.drop_table("app_user")
