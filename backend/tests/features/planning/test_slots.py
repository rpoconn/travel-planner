"""The slot registry: every slot is well formed, and its form matches its value model."""

import pytest
from pydantic import ValidationError

from features.planning.schemas import BudgetAnswer, DatesAnswer
from features.planning.slots import SLOTS, STAGE_SLOTS, SlotSpec, slot
from features.plans.schemas import PlanStage, SlotName

INPUT_SLOTS = [spec for spec in SLOTS.values() if spec.kind == "input"]
PICK_SLOTS = [spec for spec in SLOTS.values() if spec.kind == "pick"]


def test_every_slot_name_is_registered_once() -> None:
    assert set(SLOTS) == set(SlotName)


def test_stages_ask_their_slots_in_order() -> None:
    assert STAGE_SLOTS[PlanStage.BASICS] == (
        SlotName.DESTINATION,
        SlotName.DATES,
        SlotName.TRAVELERS,
        SlotName.ORIGIN,
        SlotName.BUDGET,
    )
    assert STAGE_SLOTS[PlanStage.FLIGHTS] == (
        SlotName.FLIGHT_PREFERENCES,
        SlotName.FLIGHT_OUTBOUND,
        SlotName.FLIGHT_RETURN,
    )
    assert STAGE_SLOTS[PlanStage.REVIEW] == ()


def test_only_the_budget_is_optional() -> None:
    assert [spec.name for spec in SLOTS.values() if spec.optional] == [SlotName.BUDGET]


@pytest.mark.parametrize("spec", INPUT_SLOTS, ids=lambda spec: spec.name)
def test_an_input_slot_asks_for_every_required_value(spec: SlotSpec) -> None:
    assert spec.value_model is not None
    model_fields = spec.value_model.model_fields
    form = {field.name: field for field in spec.fields}

    # Every form field is a key of the value...
    assert set(form) <= set(model_fields)
    # ...every required value has a required form field...
    for name, info in model_fields.items():
        if info.is_required():
            assert name in form and form[name].required, name
    # ...and it has the words the engine and the extractor need.
    assert spec.prompt and spec.nudge and spec.extractor_hint


@pytest.mark.parametrize("spec", INPUT_SLOTS, ids=lambda spec: spec.name)
def test_every_choice_option_is_a_valid_value(spec: SlotSpec) -> None:
    assert spec.value_model is not None
    for field in spec.fields:
        if field.kind not in ("choice", "multi_choice"):
            continue
        for option in field.options:
            value = [option.value] if field.kind == "multi_choice" else option.value
            # Fill the model's other required fields from their first option.
            others = {
                other.name: [other.options[0].value]
                if other.kind == "multi_choice"
                else other.options[0].value
                for other in spec.fields
                if other.name != field.name and other.options
            }
            spec.value_model.model_validate({**others, field.name: value})


@pytest.mark.parametrize("spec", PICK_SLOTS, ids=lambda spec: spec.name)
def test_a_pick_slot_has_no_form(spec: SlotSpec) -> None:
    assert spec.value_model is None
    assert spec.fields == ()
    assert 1 <= spec.min_picks <= spec.max_picks


def test_activities_allow_several_picks() -> None:
    assert slot(SlotName.ACTIVITIES).max_picks > 1
    assert slot(SlotName.FLIGHT_OUTBOUND).max_picks == 1


def test_dates_must_be_in_order() -> None:
    DatesAnswer.model_validate({"dates": {"start": "2027-05-01", "end": "2027-05-01"}})
    with pytest.raises(ValidationError, match="on or after"):
        DatesAnswer.model_validate({"dates": {"start": "2027-05-04", "end": "2027-05-01"}})


def test_a_budget_must_be_more_than_zero() -> None:
    assert BudgetAnswer.model_validate({"budget": "$3,000"}).budget.amount == 3000
    with pytest.raises(ValidationError, match="skip it"):
        BudgetAnswer.model_validate({"budget": 0})
