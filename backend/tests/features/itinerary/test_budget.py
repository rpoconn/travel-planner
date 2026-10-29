from decimal import Decimal

from features.itinerary.budget import (
    cost_error,
    limit_error,
    needs_cost,
    party_size,
    total_error,
)
from features.itinerary.schemas import ActivityItem, SpendingLimit
from shared.domain import Cost, Money, Travelers
from tests.features.itinerary.factories import activity, lodging

USD_1000 = Money(amount=Decimal(1000))
FOOD_200 = SpendingLimit(category="food", max_per_person=Money(amount=Decimal(200)))


def _dinner(amount: str | None) -> ActivityItem:
    item = activity("2027-05-03T19:00", "2027-05-03T21:00", title="Dinner")
    cost = Cost(amount=Decimal(amount)) if amount is not None else None
    return item.model_copy(update={"category": "food", "cost": cost})


def test_party_size_counts_children_and_defaults_to_one() -> None:
    assert party_size(Travelers(adults=2, children=1)) == 3
    assert party_size(None) == 1


def test_only_budgets_and_limited_categories_need_costs() -> None:
    hotel = lodging("2027-05-01T15:00", "2027-05-08T11:00")

    assert needs_cost(hotel, USD_1000, [])
    assert not needs_cost(hotel, None, [FOOD_200])
    assert needs_cost(_dinner(None), None, [FOOD_200])


def test_cost_error() -> None:
    assert cost_error(_dinner(None), USD_1000) == "Dinner has no cost"
    assert cost_error(_dinner("50"), USD_1000) is None
    assert cost_error(_dinner("50"), None) is None


def test_limit_is_per_person_and_unknown_travelers_count_as_one() -> None:
    dinner = _dinner("300")

    assert limit_error(dinner, [FOOD_200], Travelers(adults=2)) is None
    error = limit_error(dinner, [FOOD_200], None)
    assert error is not None and "(300.00 USD a person for 1)" in error


def test_limit_ignores_other_categories() -> None:
    sightseeing = _dinner("999").model_copy(update={"category": "sightseeing"})

    assert limit_error(sightseeing, [FOOD_200], None) is None


def test_total_error() -> None:
    costs = [Cost(amount=Decimal(600)), None, Cost(amount=Decimal(500))]

    assert total_error(None, costs) is None
    assert total_error(Money(amount=Decimal(1100)), costs) is None
    assert total_error(USD_1000, costs) == (
        "costs total 1,100.00 USD, 100.00 USD over the 1,000.00 USD budget"
    )
    # Costs in another currency are not counted (cost_error reports them).
    assert total_error(USD_1000, [Cost(amount=Decimal(99999), currency="JPY")]) is None
