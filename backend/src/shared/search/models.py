from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, UTCDateTime, utc_now


class SearchCacheEntry(Base):
    """One cached search response, keyed by a hash of (kind, normalized query, max_results)."""

    __tablename__ = "search_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    query: Mapped[str] = mapped_column(Text)
    # list[SearchResult] as plain dicts; validated on the way out.
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
