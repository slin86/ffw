"""Email address on app_user, plus one-time tokens for verify and reset.

`app_user.email` is nullable on purpose: accounts that existed before mail was
added have none, and an admin creating a user with the CLI may leave it out.
A unique index still applies, but NULLs do not collide in Postgres, so any
number of address-less accounts can coexist.

Revision ID: 0002_email_and_tokens
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_email_and_tokens"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("email", sa.String(), nullable=True))
    op.add_column(
        "app_user",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_app_user_email", "app_user", ["email"])

    op.create_table(
        "user_token",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        # SHA-256 hex digest. The raw token is never stored - it exists only in
        # the mail that was sent.
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("purpose IN ('VERIFY', 'RESET')", name="chk_user_token_purpose"),
    )
    # Every lookup is "the live tokens of this user for this purpose".
    op.create_index("ix_user_token_user_purpose", "user_token", ["user_id", "purpose"])


def downgrade() -> None:
    op.drop_index("ix_user_token_user_purpose", table_name="user_token")
    op.drop_table("user_token")
    op.drop_constraint("uq_app_user_email", "app_user", type_="unique")
    op.drop_column("app_user", "email_verified_at")
    op.drop_column("app_user", "email")
