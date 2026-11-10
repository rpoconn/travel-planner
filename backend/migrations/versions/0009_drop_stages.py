"""Drop the planning stages: a plan has problems to fix, not a position in a workflow.

A plan keeps whether it is made without flying, under the name the planner uses
(`no_flights`). Stage dividers leave the transcript, and what a stage command did
("Back to flights") stays as a plain message. Open offers are dismissed: the planner
searches again from the plan's problems.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The payload is JSON; cast so the match works on SQLite and PostgreSQL alike.
_STAGE_COMMANDS = (
    "kind = 'changes' AND (CAST(payload AS TEXT) LIKE '%change_stage%' "
    "OR CAST(payload AS TEXT) LIKE '%redo_search%')"
)


def upgrade() -> None:
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.drop_column("stage")
        batch_op.alter_column("flights_skipped", new_column_name="no_flights")
    with op.batch_alter_table("planning_offers", schema=None) as batch_op:
        batch_op.drop_column("stage")

    op.execute("DELETE FROM chat_messages WHERE kind = 'stage'")
    # Their payloads name commands that no longer exist; the words still read on their own.
    op.execute(f"UPDATE chat_messages SET kind = 'message', payload = NULL WHERE {_STAGE_COMMANDS}")
    op.execute(
        "UPDATE planning_offers SET status = 'dismissed', decided_at = CURRENT_TIMESTAMP "
        "WHERE status = 'open'"
    )


def downgrade() -> None:
    # Deleted dividers and dismissed offers don't come back, and every plan restarts at
    # the first stage; only the schema returns.
    with op.batch_alter_table("planning_offers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("stage", sa.String(length=20), nullable=False, server_default="basics")
        )
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.alter_column("no_flights", new_column_name="flights_skipped")
        batch_op.add_column(
            sa.Column("stage", sa.String(length=20), nullable=False, server_default="basics")
        )
