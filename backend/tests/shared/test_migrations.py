"""Alembic migrations: they build the schema the ORM models describe, and adopt databases
that `create_all` built before migrations existed."""

from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.db import Base, create_engine
from shared.migrations import BASELINE_REVISION, alembic_config, run_migrations

_BASELINE_TABLES = frozenset(
    {"users", "travel_plans", "itinerary_items", "chat_messages", "search_cache"}
)
# Columns later migrations added to the baseline tables: create_all builds today's models,
# so these are dropped again to get the pre-migration schema.
_LATER_COLUMNS = {"travel_plans": ("limits",)}


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    yield engine
    await engine.dispose()


def _head() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _schema_drift(connection: Connection) -> list[object]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, Base.metadata))


async def test_migrations_build_the_model_schema(engine: AsyncEngine) -> None:
    await run_migrations(engine)

    async with engine.connect() as connection:
        assert await connection.run_sync(_current_revision) == _head()
        # An empty diff means every model change has a migration.
        assert await connection.run_sync(_schema_drift) == []


async def test_running_migrations_twice_is_a_no_op(engine: AsyncEngine) -> None:
    await run_migrations(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO search_cache VALUES ('k', 'general', 'q', '[]', '2026-01-01')")
        )

    await run_migrations(engine)

    async with engine.connect() as connection:
        count = await connection.scalar(text("SELECT count(*) FROM search_cache"))
    assert count == 1


async def test_pre_migration_database_is_adopted(engine: AsyncEngine) -> None:
    # What Phases 1-5 left behind: the baseline tables from create_all, no alembic_version.
    # (Tables added by later migrations didn't exist yet.)
    baseline = [table for name, table in Base.metadata.tables.items() if name in _BASELINE_TABLES]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=baseline)
        for table, columns in _LATER_COLUMNS.items():
            for column in columns:
                await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))

    await run_migrations(engine)

    async with engine.connect() as connection:
        assert await connection.run_sync(_current_revision) == _head()


async def test_downgrade_to_base_and_back(engine: AsyncEngine) -> None:
    await run_migrations(engine)

    def downgrade(connection: Connection) -> list[str]:
        command.downgrade(alembic_config(connection), "base")
        return inspect(connection).get_table_names()

    async with engine.begin() as connection:
        assert await connection.run_sync(downgrade) == ["alembic_version"]

    await run_migrations(engine)
    async with engine.connect() as connection:
        assert await connection.run_sync(_current_revision) == _head()


def test_baseline_is_the_first_revision() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_base() == BASELINE_REVISION
