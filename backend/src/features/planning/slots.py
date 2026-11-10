"""Slot registry: every input the planning flow asks for, in the order a trip is usually
planned: the basics, the flights, the stays, the activities, then the rides between them.

A slot is either an `input` (a form the user fills in, with the slot's `value_model` as its
answer) or a `pick` (choosing from an offer's candidates). A slot is the thing that fixes a
problem: `fixes` names the problem it resolves. Input slots that fix nothing (preferences,
the budget) hold what a search needs; `read` finds the plan's answer for them.
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
    kind: SlotKind
    prompt: str  # the question, shown above the form or the choices
    # The problem this slot resolves.
    fixes: IssueCode | None = None
    # input slots: what the form must produce, and the plan's answer.
    value_model: type[BaseModel] | None = None
    read: SlotReader | None = None
    fields: tuple[FieldSpec, ...] = ()
    optional: bool = False  # the user may skip it (recorded in `PlanPreferences.declined`)
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
        kind="input",
        fixes=IssueCode.MISSING_DESTINATION,
        prompt="Where are you going?",
        value_model=DestinationAnswer,
        read=_read_destination,
        fields=(
            FieldSpec(name="destination", label="Destination", kind="place", placeholder="Lisbon"),
        ),
    ),
    SlotSpec(
        name=SlotName.DATES,
        kind="input",
        fixes=IssueCode.MISSING_DATES,
        prompt="When are you going?",
        value_model=DatesAnswer,
        read=_read_dates,
        fields=(FieldSpec(name="dates", label="Trip dates", kind="date_range"),),
    ),
    SlotSpec(
        name=SlotName.TRAVELERS,
        kind="input",
        fixes=IssueCode.MISSING_TRAVELERS,
        prompt="Who's coming?",
        value_model=Travelers,
        read=_read_travelers,
        fields=(
            FieldSpec(name="adults", label="Adults", kind="integer", min=1, max=20),
            FieldSpec(
                name="children", label="Children", kind="integer", required=False, min=0, max=20
            ),
        ),
    ),
    SlotSpec(
        name=SlotName.ORIGIN,
        kind="input",
        fixes=IssueCode.MISSING_ORIGIN,
        prompt="Where does the trip start?",
        value_model=OriginAnswer,
        read=_read_origin,
        fields=(FieldSpec(name="origin", label="Leaving from", kind="place", placeholder="Home"),),
    ),
    SlotSpec(
        name=SlotName.BUDGET,
        kind="input",
        prompt="Do you have a budget for the whole trip?",
        value_model=BudgetAnswer,
        read=_read_budget,
        fields=(FieldSpec(name="budget", label="Total budget", kind="money"),),
        optional=True,
    ),
    # ----- flights -----
    SlotSpec(
        name=SlotName.FLIGHT_PREFERENCES,
        kind="input",
        prompt="How do you like to fly?",
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
    ),
    SlotSpec(
        name=SlotName.FLIGHT_OUTBOUND,
        kind="pick",
        fixes=IssueCode.MISSING_OUTBOUND,
        prompt="Pick your flight out.",
    ),
    SlotSpec(
        name=SlotName.FLIGHT_RETURN,
        kind="pick",
        fixes=IssueCode.MISSING_RETURN,
        prompt="Pick your flight home.",
    ),
    # ----- lodging -----
    SlotSpec(
        name=SlotName.LODGING_PREFERENCES,
        kind="input",
        prompt="What kind of place do you want to stay in?",
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
    ),
    SlotSpec(
        name=SlotName.LODGING_STAY,
        kind="pick",
        fixes=IssueCode.UNCOVERED_NIGHT,
        prompt="Pick a place to stay.",
    ),
    # ----- activities -----
    SlotSpec(
        name=SlotName.ACTIVITY_PREFERENCES,
        kind="input",
        prompt="What do you like to do?",
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
    ),
    SlotSpec(
        name=SlotName.ACTIVITIES,
        kind="pick",
        fixes=IssueCode.NO_ACTIVITIES,
        prompt="Pick the things you'd like to do.",
        max_picks=8,
    ),
    # ----- ground -----
    SlotSpec(
        name=SlotName.TRANSFER,
        kind="pick",
        fixes=IssueCode.MISSING_TRANSFER,
        prompt="How do you want to get there?",
        # Transfers come from rules that list every way across at once.
        refreshable=False,
    ),
)

SLOTS: dict[SlotName, SlotSpec] = {spec.name: spec for spec in _SLOTS}

# The slot that fixes each blocking problem.
FIX_SLOT: dict[IssueCode, SlotName] = {
    spec.fixes: spec.name for spec in _SLOTS if spec.fixes is not None
}

# The order slots are listed in, for sorting the problems they fix.
SLOT_ORDER: dict[SlotName, int] = {spec.name: index for index, spec in enumerate(_SLOTS)}


def slot(name: SlotName) -> SlotSpec:
    return SLOTS[name]
