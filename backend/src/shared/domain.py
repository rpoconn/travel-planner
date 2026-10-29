"""Value objects shared across features (plans and itinerary items).

These are embedded values (stored as JSON columns), not request/response schemas.
"""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_core import from_json


class Place(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=200)
    iata: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")  # airports only
    address: str | None = Field(default=None, max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="before")
    @classmethod
    def _from_name(cls, data: Any) -> Any:
        # Small models often send a place as a bare string ("Tokyo"), or as the object
        # JSON-encoded into a string. Accept both. A "before" validator leaves the JSON
        # schema unchanged, so tool schemas and the OpenAPI spec still ask for an object.
        if not isinstance(data, str):
            return data
        if data.lstrip().startswith("{"):
            try:
                return from_json(data)
            except ValueError:
                pass  # not actually JSON; treat it as a name
        return {"name": data}


class Money(BaseModel):
    amount: Decimal = Field(ge=0, le=1_000_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")  # ISO 4217


class Travelers(BaseModel):
    adults: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=20)
