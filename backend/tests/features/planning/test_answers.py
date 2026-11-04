"""Checking slot answers and how they read in the transcript (no database)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from features.planning.answers import AnswerError, describe_answer, parse_answer
from features.planning.schemas import (
    BudgetAnswer,
    DateRange,
    DatesAnswer,
    DestinationAnswer,
    OriginAnswer,
)
from features.plans.schemas import (
    ActivityPreferences,
    FlightPreferencesAnswer,
    LodgingPreferences,
    SlotName,
)
from shared.domain import Money, Place, Travelers

TODAY = date(2026, 10, 1)


# ---------------------------------------------------------------------------
# parse_answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "value", "expected"),
    [
        (
            SlotName.DESTINATION,
            {"destination": {"name": "Lisbon"}},
            DestinationAnswer(destination=Place(name="Lisbon")),
        ),
        (
            SlotName.DATES,
            {"dates": {"start": "2027-05-01", "end": "2027-05-08"}},
            DatesAnswer(dates=DateRange(start=date(2027, 5, 1), end=date(2027, 5, 8))),
        ),
        (SlotName.TRAVELERS, {"adults": 2}, Travelers(adults=2, children=0)),
        (
            SlotName.BUDGET,
            {"budget": {"amount": "4000", "currency": "EUR"}},
            BudgetAnswer(budget=Money(amount=Decimal(4000), currency="EUR")),
        ),
        (
            SlotName.FLIGHT_PREFERENCES,
            {"cabin": "business", "max_stops": 0},
            FlightPreferencesAnswer(cabin="business", max_stops=0),
        ),
    ],
)
def test_a_valid_value_parses_into_the_slots_model(
    slot: SlotName, value: dict[str, Any], expected: BaseModel
) -> None:
    assert parse_answer(slot, value, TODAY) == expected


@pytest.mark.parametrize(
    ("slot", "value", "message"),
    [
        (
            SlotName.DATES,
            {"dates": {"start": "2027-05-08", "end": "2027-05-01"}},
            "The trip has to end on or after the day it starts",
        ),
        (
            SlotName.DATES,
            {"dates": {"start": "2026-09-01", "end": "2026-09-05"}},
            "The trip can't start in the past",
        ),
        (SlotName.TRAVELERS, {"adults": 0}, "adults: Input should be greater than or equal to 1"),
        (
            SlotName.BUDGET,
            {"budget": {"amount": 0}},
            "A budget has to be more than 0; skip it if you don't have one",
        ),
        (SlotName.ACTIVITY_PREFERENCES, {"interests": []}, "interests: List should have at least"),
        (SlotName.FLIGHT_OUTBOUND, {}, "That question is answered by picking from the list"),
    ],
)
def test_an_invalid_value_says_what_is_wrong(
    slot: SlotName, value: dict[str, Any], message: str
) -> None:
    with pytest.raises(AnswerError) as caught:
        parse_answer(slot, value, TODAY)

    assert str(caught.value).startswith(message)


# ---------------------------------------------------------------------------
# describe_answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (
            DestinationAnswer(destination=Place(name="Lisbon", country="Portugal")),
            "Lisbon, Portugal",
        ),
        (
            DatesAnswer(dates=DateRange(start=date(2027, 5, 1), end=date(2027, 5, 8))),
            "May 1, 2027 to May 8, 2027",
        ),
        (Travelers(adults=1), "1 adult"),
        (Travelers(adults=2, children=1), "2 adults, 1 child"),
        (Travelers(adults=2, children=3), "2 adults, 3 children"),
        (OriginAnswer(origin=Place(name="Philadelphia")), "From Philadelphia"),
        (BudgetAnswer(budget=Money(amount=Decimal("4000"))), "A budget of 4,000 USD"),
        (BudgetAnswer(budget=Money(amount=Decimal("99.5"))), "A budget of 99.50 USD"),
        (FlightPreferencesAnswer(), "Economy cabin, any number of stops"),
        (
            FlightPreferencesAnswer(cabin="business", max_stops=0, depart_window="morning"),
            "Business cabin, nonstop, morning departures",
        ),
        (
            LodgingPreferences(
                hotel_class="midrange",
                nightly_budget=Money(amount=Decimal(200)),
                neighborhood="Alfama",
            ),
            "Midrange hotel, up to 200 USD a night, in Alfama",
        ),
        (
            ActivityPreferences(interests=["museum", "food"], dietary="vegetarian"),
            "Museum, Food; vegetarian",
        ),
    ],
)
def test_an_answer_reads_as_one_line(answer: BaseModel, text: str) -> None:
    assert describe_answer(answer) == text
