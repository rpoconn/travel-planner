"""Chat proposals: plan changes the assistant staged for the user's approval.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29 09:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["travel_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    with op.batch_alter_table("chat_proposals", schema=None) as batch_op:
        batch_op.create_index("ix_chat_proposals_plan_status", ["plan_id", "status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("chat_proposals", schema=None) as batch_op:
        batch_op.drop_index("ix_chat_proposals_plan_status")

    op.drop_table("chat_proposals")
