"""Baseline schema: the tables Phases 1-5 created with `create_all`.

Revision ID: 0001
Revises:
Create Date: 2026-10-28 21:36:11.924849
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)

    op.create_table(
        "travel_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.JSON(), nullable=True),
        sa.Column("destination", sa.JSON(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("travelers", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_travel_plans_user_id"), ["user_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["travel_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.create_index(
            "ix_chat_messages_plan_created", ["plan_id", "created_at"], unique=False
        )

    op.create_table(
        "itinerary_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("start", sa.DateTime(), nullable=False),
        sa.Column("end", sa.DateTime(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("end_timezone", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("cost", sa.JSON(), nullable=True),
        sa.Column("booking_ref", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["travel_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("itinerary_items", schema=None) as batch_op:
        batch_op.create_index("ix_itinerary_items_plan_start", ["plan_id", "start"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("itinerary_items", schema=None) as batch_op:
        batch_op.drop_index("ix_itinerary_items_plan_start")

    op.drop_table("itinerary_items")
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_index("ix_chat_messages_plan_created")

    op.drop_table("chat_messages")
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_travel_plans_user_id"))

    op.drop_table("travel_plans")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_email"))

    op.drop_table("users")
    with op.batch_alter_table("search_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_cache_created_at"))

    op.drop_table("search_cache")
