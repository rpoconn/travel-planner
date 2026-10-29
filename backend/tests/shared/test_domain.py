from decimal import Decimal

import pytest
from pydantic import ValidationError

from shared.domain import Money, Place, Travelers


def test_place_accepts_a_bare_name() -> None:
    assert Place.model_validate("Tokyo") == Place(name="Tokyo")


def test_place_accepts_a_json_encoded_object() -> None:
    place = Place.model_validate('{"name": "Narita", "iata": "NRT"}')

    assert (place.name, place.iata) == ("Narita", "NRT")


def test_place_string_that_only_looks_like_json_is_a_name() -> None:
    assert Place.model_validate("{Tokyo").name == "{Tokyo"


def test_place_schema_still_asks_for_an_object() -> None:
    assert Place.model_json_schema()["type"] == "object"


@pytest.mark.parametrize(
    "fields",
    [{"city": "x" * 201}, {"country": "x" * 201}, {"address": "x" * 501}],
)
def test_place_text_fields_are_capped(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Place.model_validate({"name": "Somewhere", **fields})


def test_money_amount_is_capped() -> None:
    assert Money(amount=Decimal(1_000_000_000)).amount == Decimal(1_000_000_000)
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1000000000.01"))


def test_place_with_only_an_iata_code_is_named_after_it() -> None:
    assert Place.model_validate({"iata": "LIS"}) == Place(name="LIS", iata="LIS")


def test_place_with_only_a_city_is_named_after_it() -> None:
    assert Place.model_validate({"city": "Tokyo"}) == Place(name="Tokyo", city="Tokyo")


def test_money_accepts_a_bare_amount() -> None:
    assert Money.model_validate(742) == Money(amount=Decimal(742), currency="USD")


def test_money_does_not_read_a_bool_as_an_amount() -> None:
    with pytest.raises(ValidationError):
        Money.model_validate(True)


@pytest.mark.parametrize(
    ("raw", "amount", "currency"),
    [
        ("$1,380", "1380", "USD"),
        ("1,380.50 USD", "1380.50", "USD"),
        ("EUR 900", "900", "EUR"),
        ("¥120000", "120000", "JPY"),
        ({"total": "1,380"}, "1380", "USD"),
        ({"price": 900, "currency": "EUR"}, "900", "EUR"),
        ({"amount": "$1,200", "currency": "GBP"}, "1200", "GBP"),
    ],
)
def test_money_accepts_the_shapes_small_models_send(
    raw: object, amount: str, currency: str
) -> None:
    assert Money.model_validate(raw) == Money(amount=Decimal(amount), currency=currency)


def test_money_without_a_number_is_still_rejected() -> None:
    with pytest.raises(ValidationError):
        Money.model_validate("free")
    with pytest.raises(ValidationError, match="amount"):
        Money.model_validate({"description": "Total trip budget", "type": "object"})


def test_travelers_need_an_explicit_adult_count() -> None:
    # No default: an unknown party size is `travelers: None` on the plan, never "1 adult".
    with pytest.raises(ValidationError, match="adults"):
        Travelers.model_validate({})
    assert Travelers.model_validate({"adults": 2}) == Travelers(adults=2, children=0)
