"""The slot registry: every slot is well formed, and its form matches its value model."""

import pytest
from pydantic import ValidationError

from features.planning.schemas import BudgetAnswer, DatesAnswer
from features.planning.searches import RUNNERS
from features.planning.slots import FIX_SLOT, SLOTS, STAGE_SLOTS, SlotSpec, slot
from features.plans.schemas import PlanStage, SlotName
from shared.domain import IssueCode
from tests.features.plans.builders import snapshot

INPUT_SLOTS = [spec for spec in SLOTS.values() if spec.kind == "input"]
PICK_SLOTS = [spec for spec in SLOTS.values() if spec.kind == "pick"]

# Stage checklist issues (`plans.stages`). Review's `stage_incomplete` is fixed in the
# stage it names, so no slot fixes it.
CHECKLIST_CODES = {
    IssueCode.MISSING_DATES,
    IssueCode.MISSING_TRAVELERS,
    IssueCode.MISSING_ORIGIN,
    IssueCode.MISSING_DESTINATION,
    IssueCode.MISSING_OUTBOUND,
    IssueCode.MISSING_RETURN,
    IssueCode.UNCOVERED_NIGHT,
    IssueCode.NO_ACTIVITIES,
    IssueCode.MISSING_TRANSFER,
}
FULL_PREFERENCES = {
    "flights": {"cabin": "business", "outbound_token": "token"},
    "lodging": {"hotel_class": "midrange"},
    "activities": {"interests": ["food"]},
}


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


@pytest.mark.parametrize("code", sorted(CHECKLIST_CODES), ids=str)
def test_every_checklist_issue_has_exactly_one_fix_slot(code: IssueCode) -> None:
    fixers = [spec.name for spec in SLOTS.values() if spec.fixes == code]

    assert len(fixers) == 1
    assert FIX_SLOT[code] == fixers[0]


def test_only_checklist_issues_have_fix_slots() -> None:
    assert set(FIX_SLOT) == CHECKLIST_CODES


@pytest.mark.parametrize("spec", PICK_SLOTS, ids=lambda spec: spec.name)
def test_every_pick_slot_has_a_runner_and_fixes_an_issue(spec: SlotSpec) -> None:
    assert spec.name in RUNNERS
    assert spec.fixes is not None


@pytest.mark.parametrize("spec", INPUT_SLOTS, ids=lambda spec: spec.name)
def test_every_input_slot_reads_its_answer_from_the_plan(spec: SlotSpec) -> None:
    assert spec.read is not None and spec.value_model is not None
    full = snapshot(budget={"amount": 3000}, preferences=FULL_PREFERENCES)
    empty = snapshot(
        destination=None,
        origin=None,
        start_date=None,
        end_date=None,
        travelers=None,
    )

    answer = spec.read(full)

    assert isinstance(answer, spec.value_model)
    assert spec.read(empty) is None


def test_reading_flight_preferences_leaves_out_the_search_token() -> None:
    read = slot(SlotName.FLIGHT_PREFERENCES).read
    assert read is not None

    answer = read(snapshot(preferences=FULL_PREFERENCES))

    assert answer is not None
    assert answer.model_dump(mode="json") == {
        "cabin": "business",
        "max_stops": None,
        "depart_window": "any",
    }


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
