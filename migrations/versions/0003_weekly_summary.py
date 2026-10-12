"""weekly summary

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE reminder_kind ADD VALUE IF NOT EXISTS 'weekly_summary'")
    op.add_column(
        "users",
        sa.Column("weekly_summary_delay", sa.Integer(), nullable=False, server_default="30"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM reminders WHERE kind = 'weekly_summary'")
    op.drop_column("users", "weekly_summary_delay")
