"""Planning proposals: changes the planner suggests, waiting for Approve / Reject.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["travel_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("planning_proposals", schema=None) as batch_op:
        batch_op.create_index(
            "ix_planning_proposals_plan_status", ["plan_id", "status"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("planning_proposals", schema=None) as batch_op:
        batch_op.drop_index("ix_planning_proposals_plan_status")
    op.drop_table("planning_proposals")
