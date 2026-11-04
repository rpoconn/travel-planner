from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import sqlite

from shared.db import UTCDateTime, create_engine

_DIALECT = sqlite.dialect()


def test_utc_datetime_stores_naive_utc_and_returns_aware() -> None:
    column = UTCDateTime()
    eastern = datetime(2026, 5, 1, 8, 0, tzinfo=timezone(timedelta(hours=-4)))

    stored = column.process_bind_param(eastern, _DIALECT)
    loaded = column.process_result_value(stored, _DIALECT)

    assert stored == datetime(2026, 5, 1, 12, 0)
    assert loaded == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def test_utc_datetime_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UTCDateTime().process_bind_param(datetime(2026, 5, 1, 12, 0), _DIALECT)


async def test_a_file_database_uses_write_ahead_logging(tmp_path: Path) -> None:
    # Readers then don't block a writer's commit, so concurrent turns don't lock each other.
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}")
    try:
        async with engine.connect() as connection:
            mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
            keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
    finally:
        await engine.dispose()

    assert (mode, keys) == ("wal", 1)
