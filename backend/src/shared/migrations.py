"""Run Alembic migrations from the app (on startup) against the app's own engine.

Databases created before migrations existed (Phases 1-5 used `create_all`) have the
baseline schema but no `alembic_version` table; they are stamped at the baseline first so
the upgrade doesn't try to create tables that are already there.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# backend/alembic.ini (this file is backend/src/shared/migrations.py).
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
# The revision that matches the schema `create_all` used to build.
BASELINE_REVISION = "0001"


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config(ALEMBIC_INI)
    if connection is not None:
        # Picked up by migrations/env.py instead of opening its own engine.
        config.attributes["connection"] = connection
    return config


def _upgrade(connection: Connection) -> None:
    config = alembic_config(connection)
    tables = set(inspect(connection).get_table_names())
    if "users" in tables and "alembic_version" not in tables:
        logger.info("Adopting a pre-migration database: stamping %s", BASELINE_REVISION)
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")


async def run_migrations(engine: AsyncEngine) -> None:
    """Bring the database schema up to date (`alembic upgrade head`)."""
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)
    logger.info("Database migrated (%s)", engine.url.render_as_string(hide_password=True))
