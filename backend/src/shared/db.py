"""Async SQLAlchemy wiring: declarative base, engine factory, session dependency, column types."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import DateTime, Dialect, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Declarative base + shared column types
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base class for every ORM model (all features share one metadata)."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on any backend.

    SQLite has no timezone support and hands back naive datetimes, so values are stored as
    naive UTC and re-tagged with UTC on the way out. Naive inputs are rejected outright.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Engine / session factories
# ---------------------------------------------------------------------------


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    # SQLite ignores FOREIGN KEY / ON DELETE CASCADE unless enabled per connection.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine(database_url: str) -> AsyncEngine:
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        # Every new connection to `:memory:` is a brand-new empty database, so pin the whole
        # engine to a single shared connection (used by tests).
        engine = create_async_engine(
            database_url,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_async_engine(database_url)

    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: async sessions cannot lazy-load, so keep attributes readable
    # after commit when building response models.
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables that don't exist yet. Alembic migrations replace this in Phase 6."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ensured (%s)", engine.url.render_as_string(hide_password=True))


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, from the sessionmaker stored on the app in `create_app`."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
