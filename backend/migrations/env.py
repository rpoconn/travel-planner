"""Alembic environment.

Two ways in:
- the app (`shared.migrations.run_migrations`) passes an open connection in
  `config.attributes["connection"]`, so startup migrations reuse the app's engine (which
  matters for `:memory:` databases in tests);
- the CLI (`uv run alembic ...`) builds its own async engine from `DATABASE_URL`.
"""

import asyncio
from logging.config import fileConfig
from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy.engine import Connection

from features.auth import models as auth_models
from features.chat import models as chat_models
from features.itinerary import models as itinerary_models
from features.plans import models as plans_models
from shared.config import get_settings
from shared.db import Base, UTCDateTime, create_engine
from shared.search import models as search_models

# Every model module must be imported so `Base.metadata` knows all tables. Listed here so
# the imports count as used (a bare side-effect import is easy for linters to drop).
MODEL_MODULES = (auth_models, chat_models, itinerary_models, plans_models, search_models)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

config = context.config
target_metadata = Base.metadata

# Only configure logging for CLI runs; the app has already set up its own.
if config.attributes.get("connection") is None and config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _render_item(type_: str, obj: Any, _autogen_context: AutogenContext) -> str | Literal[False]:
    # UTCDateTime is a TypeDecorator over DateTime that only changes Python-side conversion;
    # migrations don't need to import it.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        # SQLite can't ALTER most things in place; batch mode rebuilds the table instead.
        render_as_batch=True,
        render_item=_render_item,
        compare_type=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    _configure(url=get_settings().database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_cli_online() -> None:
    engine = create_engine(get_settings().database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    connection: Connection | None = config.attributes.get("connection")
    if connection is not None:
        _run_sync(connection)
    else:
        asyncio.run(_run_cli_online())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
