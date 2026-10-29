"""Travelers optional: a plan's traveler count is unknown until the user gives it.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.alter_column("travelers", existing_type=sa.JSON(), nullable=True)


def downgrade() -> None:
    # The old schema requires a value; one adult was the old default.
    op.execute(
        'UPDATE travel_plans SET travelers = \'{"adults": 1, "children": 0}\' '
        "WHERE travelers IS NULL OR travelers = 'null'"
    )
    with op.batch_alter_table("travel_plans", schema=None) as batch_op:
        batch_op.alter_column("travelers", existing_type=sa.JSON(), nullable=False)
