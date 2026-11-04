"""Run Alembic migrations from the app (on startup) against the app's own engine.

Databases created before migrations existed (Phases 1-5 used `create_all`) have the
baseline schema but no `alembic_version` table; they are stamped at the baseline first so
the upgrade doesn't try to create tables that are already there.

On SQLite, migrations run with foreign keys off (see `migration_transaction`).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
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


class ForeignKeyViolationError(RuntimeError):
    """A migration left rows pointing at rows that no longer exist; nothing was committed."""


def _pragma(connection: Connection, statement: str) -> None:
    # PRAGMA foreign_keys is silently ignored inside a transaction, so run it on its own
    # and commit straight away (the driver doesn't open a SQLite transaction for a PRAGMA).
    connection.exec_driver_sql(statement)
    connection.commit()


@contextmanager
def migration_transaction(connection: Connection) -> Iterator[None]:
    """One transaction for a migration run, with SQLite's foreign keys off inside it.

    SQLite can't alter most columns in place, so batch migrations rebuild the table: create
    a copy, copy the rows, DROP the original, rename. With foreign keys on (the app turns
    them on for every connection), that DROP deletes every row that references the table
    through ON DELETE CASCADE: rebuilding `travel_plans` would wipe all itinerary items,
    and messages. With them off the rows survive; `foreign_key_check` then makes
    sure nothing was left dangling before committing. `connection` must not be in a
    transaction yet.
    """
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        _pragma(connection, "PRAGMA foreign_keys=OFF")
    try:
        with connection.begin():
            yield
            if sqlite:
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if violations:
                    # Raising inside the block rolls the migration back.
                    raise ForeignKeyViolationError(f"Migration broke foreign keys: {violations}")
    finally:
        if sqlite:
            _pragma(connection, "PRAGMA foreign_keys=ON")


def _upgrade(connection: Connection) -> None:
    with migration_transaction(connection):
        config = alembic_config(connection)
        tables = set(inspect(connection).get_table_names())
        if "users" in tables and "alembic_version" not in tables:
            logger.info("Adopting a pre-migration database: stamping %s", BASELINE_REVISION)
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


async def run_migrations(engine: AsyncEngine) -> None:
    """Bring the database schema up to date (`alembic upgrade head`)."""
    # `connect`, not `begin`: `migration_transaction` needs to start outside a transaction.
    async with engine.connect() as connection:
        await connection.run_sync(_upgrade)
    logger.info("Database migrated (%s)", engine.url.render_as_string(hide_password=True))
