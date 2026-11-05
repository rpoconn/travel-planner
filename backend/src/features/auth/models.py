import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, UTCDateTime, utc_now


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Stored lowercased (see service.normalize_email) so lookups are case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    password_hash: Mapped[str] = mapped_column(String(100))
    # A `shared.domain.Place`: where the user's trips start and end. Copied into each new
    # plan's origin.
    home: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
