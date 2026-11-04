"""Drop the chat flow: proposals, the web search cache, message tool traces, old kinds.

Pending proposals are dropped with their table. Messages of the chat flow's own kinds are
kept as plain text: a "redirect" (steering back to the trip) reads as a nudge, and a
"decision" (the user's answer to a proposal card) as a plain message.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_proposals", schema=None) as batch_op:
        batch_op.drop_index("ix_chat_proposals_plan_status")
    op.drop_table("chat_proposals")

    with op.batch_alter_table("search_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_cache_created_at"))
    op.drop_table("search_cache")

    op.execute("UPDATE chat_messages SET kind = 'nudge' WHERE kind = 'redirect'")
    op.execute("UPDATE chat_messages SET kind = 'message' WHERE kind = 'decision'")
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_column("tool_calls")


def downgrade() -> None:
    # The old kinds and the dropped rows can't be told apart again; only the schema returns.
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("tool_calls", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )

    op.create_table(
        "search_cache",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    with op.batch_alter_table("search_cache", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_search_cache_created_at"), ["created_at"], unique=False
        )

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
