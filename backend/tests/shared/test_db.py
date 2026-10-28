from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import sqlite

from shared.db import UTCDateTime

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
