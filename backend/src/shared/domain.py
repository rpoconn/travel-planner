"""Value objects shared across features (plans and itinerary items).

These are embedded values (stored as JSON columns), not request/response schemas.
"""

import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_core import from_json

# ---------------------------------------------------------------------------
# Place
# ---------------------------------------------------------------------------


class Place(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=200)
    iata: str | None = Field(
        default=None, pattern=r"^[A-Z]{3}$", description="3-letter airport code (airports only)"
    )
    address: str | None = Field(default=None, max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="before")
    @classmethod
    def _from_name(cls, data: object) -> object:
        # Small models often send a place as a bare string ("Tokyo"), or as the object
        # JSON-encoded into a string. Accept both. A "before" validator leaves the JSON
        # schema unchanged, so tool schemas and the OpenAPI spec still ask for an object.
        if isinstance(data, str):
            if data.lstrip().startswith("{"):
                try:
                    data = from_json(data)
                except ValueError:
                    return {"name": data}  # not actually JSON; treat it as a name
            else:
                return {"name": data}
        # They also send airports as just {"iata": "LIS"} and cities as {"city": "Tokyo"};
        # the code or city is a fine name.
        if isinstance(data, dict) and not data.get("name"):
            name = data.get("iata") or data.get("city")
            if name:
                return {**data, "name": name}
        return data


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

# Keys small models use instead of `amount` ("cost": {"total": 1380}), in priority order.
_AMOUNT_ALIASES = ("total", "price", "value", "cost")
# Everything that isn't part of a number: currency symbols/codes and thousands separators.
_NON_NUMERIC = re.compile(r"[^\d.\-]")
_CURRENCY_CODE = re.compile(r"\b([A-Z]{3})\b")
_CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "¥": "JPY", "$": "USD"}


def _money_from_string(raw: str) -> str | dict[str, str]:
    """Read "$1,380", "1,380.50 USD" or "EUR 900" as {amount, currency}.

    Returns the input unchanged when it holds no number, so the normal error is reported.
    """
    number = _NON_NUMERIC.sub("", raw)
    if not number.strip("-."):
        return raw
    money: dict[str, str] = {"amount": number}
    code = _CURRENCY_CODE.search(raw)
    symbol = next((symbol for symbol in _CURRENCY_SYMBOLS if symbol in raw), None)
    if code is not None:
        money["currency"] = code.group(1)
    elif symbol is not None:
        money["currency"] = _CURRENCY_SYMBOLS[symbol]
    return money


class Money(BaseModel):
    amount: Decimal = Field(
        ge=0,
        le=1_000_000_000,
        description="A plain number such as 1380.50 (no commas, symbols or text)",
    )
    currency: str = Field(
        default="USD", pattern=r"^[A-Z]{3}$", description="ISO 4217 code: USD, EUR, JPY..."
    )

    @model_validator(mode="before")
    @classmethod
    def _from_loose_input(cls, data: object) -> object:
        # Small models send money in many shapes: a bare number (`"cost": 742`), a string
        # with separators or a symbol ("$1,380"), or an object keyed `total`/`price` instead
        # of `amount`. Accept them all; the JSON schema still asks for {amount, currency}.
        # bool is excluded since it's an int subclass.
        if isinstance(data, bool):
            return data
        if isinstance(data, int | float | Decimal):
            return {"amount": data}
        if isinstance(data, str):
            return _money_from_string(data)
        if not isinstance(data, dict):
            return data
        if "amount" not in data:
            alias = next((alias for alias in _AMOUNT_ALIASES if alias in data), None)
            if alias is not None:
                data = {**data, "amount": data[alias]}
                del data[alias]
        if isinstance(data.get("amount"), str):
            parsed = _money_from_string(data["amount"])
            if isinstance(parsed, dict):
                # An explicit currency on the object wins over one found in the string.
                data = {**parsed, **data, "amount": parsed["amount"]}
        return data


CostBasis = Literal["exact", "estimate"]


class Cost(Money):
    # An itinerary item's price, marked as quoted or guessed so the UI and the budget
    # checks can tell them apart. (No class docstring: it would land in the tool schema.)
    basis: CostBasis = Field(
        default="estimate",
        description=(
            "exact = a price the user or a search result gave; estimate = a best guess "
            "(e.g. from a $$ price range)"
        ),
    )


# ---------------------------------------------------------------------------
# Travelers
# ---------------------------------------------------------------------------


class Travelers(BaseModel):
    # No default for adults: a plan with unknown travelers has `travelers: None` instead,
    # so the planner sees the count is missing and asks rather than assuming one adult.
    adults: int = Field(ge=1, le=20)
    children: int = Field(default=0, ge=0, le=20)


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


# Shared rather than in `plans`: the itinerary timeline uses it too, and `plans` already
# imports `itinerary`, so `itinerary` importing `plans` would be a cycle.
class IssueCode(StrEnum):
    """Why a stage isn't finished (blocking) or what looks wrong (warning). The timeline's
    warnings use the same codes."""

    # Stage checklists (blocking). The timeline also warns of uncovered_night and
    # missing_transfer.
    MISSING_DATES = "missing_dates"
    MISSING_TRAVELERS = "missing_travelers"
    MISSING_ORIGIN = "missing_origin"
    MISSING_DESTINATION = "missing_destination"
    MISSING_OUTBOUND = "missing_outbound"
    MISSING_RETURN = "missing_return"
    UNCOVERED_NIGHT = "uncovered_night"
    NO_ACTIVITIES = "no_activities"
    MISSING_TRANSFER = "missing_transfer"
    STAGE_INCOMPLETE = "stage_incomplete"  # review: an earlier stage isn't done
    # Timeline warnings.
    OVERLAP = "overlap"
    OUTSIDE_TRIP = "outside_trip"
    MISSING_COST = "missing_cost"
    OVER_LIMIT = "over_limit"
    OVER_BUDGET = "over_budget"
    WRONG_TIMEZONE = "wrong_timezone"
    WRONG_DEPARTURE = "wrong_departure"
