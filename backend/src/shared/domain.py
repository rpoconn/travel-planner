"""Value objects used by more than one feature (plans now, itinerary items in Phase 2).

These are embedded values (stored as JSON columns), not request/response schemas.
"""

from decimal import Decimal

from pydantic import BaseModel, Field


class Place(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str | None = None
    country: str | None = None
    iata: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")  # airports only
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class Money(BaseModel):
    amount: Decimal = Field(ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")  # ISO 4217


class Travelers(BaseModel):
    adults: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=20)
