"""Slot registry: every input the planning flow asks for, in the order each stage asks.

A slot is either an `input` (a form the user fills in, or free text the extractor turns
into the slot's `value_model`) or a `pick` (choosing from an offer's candidates). A slot
is the thing that fixes an issue: `fixes` names the checklist issue it resolves, and the
engine asks the fix slot of the first issue left, in this order. Input slots that fix
nothing (preferences, the budget) are asked while `read` finds no answer on the plan.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel

from features.itinerary.schemas import ActivityCategory, LodgingKind
from features.planning.schemas import (
    BudgetAnswer,
    DateRange,
    DatesAnswer,
    DestinationAnswer,
    FieldOption,
    FieldSpec,
    OriginAnswer,
)
from features.plans.schemas import (
    STOP_LABELS,
    ActivityPreferences,
    DepartWindow,
    FlightPreferencesAnswer,
    HotelClass,
    LodgingPreferences,
    PlanSnapshot,
    PlanStage,
    SlotName,
)
from shared.domain import IssueCode, Travelers
from shared.flights.types import FlightCabin

SlotKind = Literal["input", "pick"]
# What the plan already holds for an input slot, as the slot's `value_model`; None when
# it holds no answer yet.
SlotReader = Callable[[PlanSnapshot], BaseModel | None]


@dataclass(frozen=True)
class SlotSpec:
    name: SlotName
    stage: PlanStage
    kind: SlotKind
    prompt: str  # the question, shown above the form or the choices
    # Stored as the assistant's reply when free text doesn't answer the slot.
    nudge: str
    # The checklist issue this slot resolves (for every stage it shows up in).
    fixes: IssueCode | None = None
    # input slots: what the form or the extractor must produce, and the plan's answer.
    value_model: type[BaseModel] | None = None
    read: SlotReader | None = None
    fields: tuple[FieldSpec, ...] = ()
    optional: bool = False  # the user may skip it (recorded in `PlanPreferences.declined`)
    # One line for the extractor's prompt: what counts as an answer.
    extractor_hint: str = ""
    # pick slots: how many candidates one pick takes.
    min_picks: int = 1
    max_picks: int = 1
    # pick slots: whether "More options" can search again for other candidates.
    refreshable: bool = True


def _options(values: Sequence[str]) -> list[FieldOption]:
    """Choice options labelled from their values: "car_rental" → "Car rental"."""
    return [FieldOption(value=v, label=v.replace("_", " ").capitalize()) for v in values]


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def _read_destination(plan: PlanSnapshot) -> DestinationAnswer | None:
    return None if plan.destination is None else DestinationAnswer(destination=plan.destination)


def _read_dates(plan: PlanSnapshot) -> DatesAnswer | None:
    # Only both dates answer the slot; a plan with one of them asks for the range again.
    if plan.start_date is None or plan.end_date is None:
        return None
    return DatesAnswer(dates=DateRange(start=plan.start_date, end=plan.end_date))


def _read_travelers(plan: PlanSnapshot) -> Travelers | None:
    return plan.travelers


def _read_origin(plan: PlanSnapshot) -> OriginAnswer | None:
    return None if plan.origin is None else OriginAnswer(origin=plan.origin)


def _read_budget(plan: PlanSnapshot) -> BudgetAnswer | None:
    return None if plan.budget is None else BudgetAnswer(budget=plan.budget)


def _read_flight_preferences(plan: PlanSnapshot) -> FlightPreferencesAnswer | None:
    flights = plan.preferences.flights
    if flights is None:
        return None
    # Only the answer's fields: the stored preferences also hold the outbound search token.
    # (Validating the stored object itself would return it as is, token and all, since it
    # is an instance of a subclass.)
    fields = set(FlightPreferencesAnswer.model_fields)
    return FlightPreferencesAnswer.model_validate(flights.model_dump(include=fields))


def _read_lodging_preferences(plan: PlanSnapshot) -> LodgingPreferences | None:
    return plan.preferences.lodging


def _read_activity_preferences(plan: PlanSnapshot) -> ActivityPreferences | None:
    return plan.preferences.activities


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_SLOTS: tuple[SlotSpec, ...] = (
    # ----- basics -----
    SlotSpec(
        name=SlotName.DESTINATION,
        stage=PlanStage.BASICS,
        kind="input",
        fixes=IssueCode.MISSING_DESTINATION,
        prompt="Where are you going?",
        nudge="Let's start with where you're going: a city, region or country.",
        value_model=DestinationAnswer,
        read=_read_destination,
        fields=(
            FieldSpec(name="destination", label="Destination", kind="place", placeholder="Lisbon"),
        ),
        extractor_hint="The city, region or country the trip goes to.",
    ),
    SlotSpec(
        name=SlotName.DATES,
        stage=PlanStage.BASICS,
        kind="input",
        fixes=IssueCode.MISSING_DATES,
        prompt="When are you going?",
        nudge="Which dates are you travelling? A start and an end date is all I need.",
        value_model=DatesAnswer,
        read=_read_dates,
        fields=(FieldSpec(name="dates", label="Trip dates", kind="date_range"),),
        extractor_hint="The first and last day of the trip, as calendar dates.",
    ),
    SlotSpec(
        name=SlotName.TRAVELERS,
        stage=PlanStage.BASICS,
        kind="input",
        fixes=IssueCode.MISSING_TRAVELERS,
        prompt="Who's coming?",
        nudge="How many adults and children are travelling?",
        value_model=Travelers,
        read=_read_travelers,
        fields=(
            FieldSpec(name="adults", label="Adults", kind="integer", min=1, max=20),
            FieldSpec(
                name="children", label="Children", kind="integer", required=False, min=0, max=20
            ),
        ),
        extractor_hint="How many adults and how many children travel.",
    ),
    SlotSpec(
        name=SlotName.ORIGIN,
        stage=PlanStage.BASICS,
        kind="input",
        fixes=IssueCode.MISSING_ORIGIN,
        prompt="Where does the trip start?",
        nudge="Where are you leaving from? Usually your home city.",
        value_model=OriginAnswer,
        read=_read_origin,
        fields=(FieldSpec(name="origin", label="Leaving from", kind="place", placeholder="Home"),),
        extractor_hint="The city or address the trip starts from and returns to.",
    ),
    SlotSpec(
        name=SlotName.BUDGET,
        stage=PlanStage.BASICS,
        kind="input",
        prompt="Do you have a budget for the whole trip?",
        nudge="What's the most you'd like to spend in total? You can skip this.",
        value_model=BudgetAnswer,
        read=_read_budget,
        fields=(FieldSpec(name="budget", label="Total budget", kind="money"),),
        optional=True,
        extractor_hint="The total amount of money for the whole trip, for everyone.",
    ),
    # ----- flights -----
    SlotSpec(
        name=SlotName.FLIGHT_PREFERENCES,
        stage=PlanStage.FLIGHTS,
        kind="input",
        prompt="How do you like to fly?",
        nudge="Before I search: which cabin, how many stops, and what time of day?",
        value_model=FlightPreferencesAnswer,
        read=_read_flight_preferences,
        fields=(
            FieldSpec(
                name="cabin", label="Cabin", kind="choice", options=_options(list(FlightCabin))
            ),
            FieldSpec(
                name="max_stops",
                label="Stops",
                kind="choice",
                required=False,
                options=[
                    FieldOption(value=stops, label=label.capitalize())
                    for stops, label in STOP_LABELS.items()
                ],
            ),
            FieldSpec(
                name="depart_window",
                label="Departure time",
                kind="choice",
                options=_options(get_args(DepartWindow)),
            ),
        ),
        extractor_hint="Cabin class, the most stops they accept, and a time of day to leave.",
    ),
    SlotSpec(
        name=SlotName.FLIGHT_OUTBOUND,
        stage=PlanStage.FLIGHTS,
        kind="pick",
        fixes=IssueCode.MISSING_OUTBOUND,
        prompt="Pick your flight out.",
        nudge='Pick one of these flights out, or tell me which one ("the second one").',
    ),
    SlotSpec(
        name=SlotName.FLIGHT_RETURN,
        stage=PlanStage.FLIGHTS,
        kind="pick",
        fixes=IssueCode.MISSING_RETURN,
        prompt="Pick your flight home.",
        nudge="Pick one of these flights home, or tell me which one.",
    ),
    # ----- lodging -----
    SlotSpec(
        name=SlotName.LODGING_PREFERENCES,
        stage=PlanStage.LODGING,
        kind="input",
        prompt="What kind of place do you want to stay in?",
        nudge="Before I search: what class of hotel, a nightly budget, and a neighborhood?",
        value_model=LodgingPreferences,
        read=_read_lodging_preferences,
        fields=(
            FieldSpec(
                name="hotel_class",
                label="Class",
                kind="choice",
                options=_options(get_args(HotelClass)),
            ),
            FieldSpec(
                name="kind", label="Type", kind="choice", options=_options(get_args(LodgingKind))
            ),
            FieldSpec(
                name="nightly_budget", label="Per night, up to", kind="money", required=False
            ),
            FieldSpec(
                name="neighborhood",
                label="Neighborhood",
                kind="text",
                required=False,
                placeholder="Anywhere central",
            ),
        ),
        extractor_hint="Hotel class, type of lodging, a per-night budget and a neighborhood.",
    ),
    SlotSpec(
        name=SlotName.LODGING_STAY,
        stage=PlanStage.LODGING,
        kind="pick",
        fixes=IssueCode.UNCOVERED_NIGHT,
        prompt="Pick a place to stay.",
        nudge="Pick one of these places to stay, or tell me which one.",
    ),
    # ----- activities -----
    SlotSpec(
        name=SlotName.ACTIVITY_PREFERENCES,
        stage=PlanStage.ACTIVITIES,
        kind="input",
        prompt="What do you like to do?",
        nudge="Pick up to five kinds of things you'd like to do, and any dietary needs.",
        value_model=ActivityPreferences,
        read=_read_activity_preferences,
        fields=(
            FieldSpec(
                name="interests",
                label="Interests",
                kind="multi_choice",
                options=_options(get_args(ActivityCategory)),
                min=1,
                max=5,
            ),
            FieldSpec(
                name="dietary",
                label="Dietary needs",
                kind="text",
                required=False,
                placeholder="Vegetarian, no shellfish...",
            ),
        ),
        extractor_hint="Kinds of activities they enjoy, and any dietary restrictions.",
    ),
    SlotSpec(
        name=SlotName.ACTIVITIES,
        stage=PlanStage.ACTIVITIES,
        kind="pick",
        fixes=IssueCode.NO_ACTIVITIES,
        prompt="Pick the things you'd like to do.",
        nudge="Pick at least one of these, or tell me which ones.",
        max_picks=8,
    ),
    # ----- ground -----
    SlotSpec(
        name=SlotName.TRANSFER,
        stage=PlanStage.GROUND,
        kind="pick",
        fixes=IssueCode.MISSING_TRANSFER,
        prompt="How do you want to get there?",
        nudge="Pick one of these ways to get there, or tell me which one.",
        # Transfers come from rules that list every way across at once.
        refreshable=False,
    ),
)

SLOTS: dict[SlotName, SlotSpec] = {spec.name: spec for spec in _SLOTS}

# Each stage's slots in the order they're asked. Review asks nothing.
STAGE_SLOTS: dict[PlanStage, tuple[SlotName, ...]] = {
    stage: tuple(spec.name for spec in _SLOTS if spec.stage == stage) for stage in PlanStage
}


# The slot that fixes each checklist issue. Review's `stage_incomplete` has none: it is
# fixed in the stage it names.
FIX_SLOT: dict[IssueCode, SlotName] = {
    spec.fixes: spec.name for spec in _SLOTS if spec.fixes is not None
}

# The order slots are asked in, for sorting the issues they fix.
SLOT_ORDER: dict[SlotName, int] = {spec.name: index for index, spec in enumerate(_SLOTS)}


def slot(name: SlotName) -> SlotSpec:
    return SLOTS[name]
