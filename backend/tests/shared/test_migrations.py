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
from shared.migrations import (
    BASELINE_REVISION,
    alembic_config,
    migration_transaction,
    run_migrations,
)

_BASELINE_TABLES = frozenset(
    {"users", "travel_plans", "itinerary_items", "chat_messages", "search_cache"}
)
# Columns later migrations added to the baseline tables: create_all builds today's models,
# so these are dropped again to get the pre-migration schema.
_LATER_COLUMNS = {
    "travel_plans": ("limits", "stage", "flights_skipped"),
    "users": ("home",),
}


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
        _migrate(connection, "base", down=True)
        return inspect(connection).get_table_names()

    async with engine.connect() as connection:
        assert await connection.run_sync(downgrade) == ["alembic_version"]

    await run_migrations(engine)
    async with engine.connect() as connection:
        assert await connection.run_sync(_current_revision) == _head()


# ---------------------------------------------------------------------------
# Data survives table rebuilds
# ---------------------------------------------------------------------------
# SQLite batch migrations rebuild a table (copy, DROP the original, rename). With foreign
# keys on, that DROP deletes every child row through ON DELETE CASCADE, so a plan rebuild
# used to wipe its items, messages and proposals.

_CHILD_TABLES = ("itinerary_items", "chat_messages")
_NOW = "2026-10-01 12:00:00.000000"


def _migrate(connection: Connection, revision: str, *, down: bool = False) -> None:
    with migration_transaction(connection):
        config = alembic_config(connection)
        (command.downgrade if down else command.upgrade)(config, revision)


async def _seed_at_0003(engine: AsyncEngine) -> None:
    """A user with one plan, one item and one chat message, in the 0003 schema."""
    async with engine.connect() as connection:
        await connection.run_sync(_migrate, "0003")
    statements = [
        f"INSERT INTO users VALUES ('{'a' * 32}', 'old@example.com', 'Old', 'x', '{_NOW}')",
        "INSERT INTO travel_plans (id, user_id, title, status, travelers, notes, version, "
        f"created_at, updated_at) VALUES ('{'b' * 32}', '{'a' * 32}', 'Old trip', 'draft', "
        f"'{{\"adults\": 2}}', '', 1, '{_NOW}', '{_NOW}')",
        'INSERT INTO itinerary_items (id, plan_id, type, title, start, "end", timezone, '
        "status, notes, created_by, details, created_at, updated_at) VALUES "
        f"('{'c' * 32}', '{'b' * 32}', 'activity', 'Museum', '{_NOW}', '{_NOW}', 'UTC', "
        f"'proposed', '', 'user', '{{}}', '{_NOW}', '{_NOW}')",
        f"INSERT INTO chat_messages VALUES ('{'d' * 32}', '{'b' * 32}', 'user', 'message', "
        f"'Hi', '[]', '{_NOW}')",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))


async def _row_counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as connection:
        return {
            table: (await connection.scalar(text(f"SELECT count(*) FROM {table}"))) or 0
            for table in ("users", "travel_plans", *_CHILD_TABLES)
        }


async def test_upgrades_keep_every_row(engine: AsyncEngine) -> None:
    await _seed_at_0003(engine)

    await run_migrations(engine)

    assert await _row_counts(engine) == {
        "users": 1,
        "travel_plans": 1,
        "itinerary_items": 1,
        "chat_messages": 1,
    }
    async with engine.connect() as connection:
        plan = (
            await connection.execute(text("SELECT stage, flights_skipped FROM travel_plans"))
        ).one()
        # Foreign keys are back on for the app's own queries.
        assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
    assert tuple(plan) == ("basics", False)


async def test_downgrades_keep_every_row(engine: AsyncEngine) -> None:
    await _seed_at_0003(engine)
    await run_migrations(engine)

    async with engine.connect() as connection:
        await connection.run_sync(_migrate, "0003", down=True)

    assert set((await _row_counts(engine)).values()) == {1}


def test_baseline_is_the_first_revision() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_base() == BASELINE_REVISION
