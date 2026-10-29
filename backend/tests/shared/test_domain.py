from decimal import Decimal

import pytest
from pydantic import ValidationError

from shared.domain import Money, Place


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
