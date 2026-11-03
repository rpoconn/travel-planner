"""Planning stages: a workflow position per plan, and a home per user.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Users set a home later. Existing plans start at the first stage, and the user clicks
    # Next through the stages they already completed.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("home", sa.JSON(), nullable=True))
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("stage", sa.String(length=20), nullable=False, server_default="basics")
        )
        batch_op.add_column(
            sa.Column("flights_skipped", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.drop_column("flights_skipped")
        batch_op.drop_column("stage")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("home")
