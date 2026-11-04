"""Planning engine: plan preferences, structured message payloads, and offers.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing plans have no preferences yet; existing messages are plain text.
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("preferences", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("payload", sa.JSON(), nullable=True))
    op.create_table(
        "planning_offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("gap", sa.JSON(), nullable=True),
        sa.Column("nights", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["travel_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("planning_offers", schema=None) as batch_op:
        batch_op.create_index("ix_planning_offers_plan_status", ["plan_id", "status"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_planning_offers_query_hash"), ["query_hash"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("planning_offers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_planning_offers_query_hash"))
        batch_op.drop_index("ix_planning_offers_plan_status")
    op.drop_table("planning_offers")
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_column("payload")
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.drop_column("preferences")
