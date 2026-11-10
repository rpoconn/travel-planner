"""What the model is shown of a plan: the trip's facts, its numbered items, what it is
waiting on the user for, and the validation engine's problems.

Pure functions (no database, no model). `TurnBrief` is the whole of a turn's first user
message, as JSON: the recent conversation, the plan, and what set the turn off. The write
tools hand the plan back in the same shape (`PlannerContext`), since a change renumbers
the items and opens or closes problems.
"""

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from features.itinerary.schemas import FlightItem, ItineraryItem, TransferItem
from features.planning.commands import numbered_items
from features.planning.schemas import (
    AskInput,
    Candidate,
    OfferChoice,
    Pending,
    Problem,
    ProblemSeverity,
    ReviewProposal,
    TravelPlan,
)
from features.planning.slots import SLOTS
from features.plans.schemas import SlotName
from shared.domain import Money, Place, Travelers

# The stored search preferences the model is shown, by the name the
# `set_*_preferences` commands and the searches know them by.
PREFERENCE_SLOTS: dict[str, SlotName] = {
    "flights": SlotName.FLIGHT_PREFERENCES,
    "lodging": SlotName.LODGING_PREFERENCES,
    "activities": SlotName.ACTIVITY_PREFERENCES,
}

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class ProblemView(BaseModel):
    """A problem as the model reads it; `id` is what `search` and "Fix this" name it by."""

    id: str
    severity: ProblemSeverity
    message: str
    how: Literal["ask", "search", "edit"]  # how it is usually fixed (`ProblemFix`)
    slot: SlotName | None = None  # ask: the form's slot; search: the pick slot searched for


class CandidateView(BaseModel):
    """A search result or an offered choice. `ref` is how `add_candidate`, `pick_choice`
    and the user's "the second one" name it."""

    ref: str
    title: str
    subtitle: str = ""
    details: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    price: str | None = None  # "1,380 USD"


class ItemView(BaseModel):
    """An itinerary item under the number commands point at it by."""

    number: int
    type: str
    title: str
    start: str  # local wall-clock time in `timezone`, as `ItemEdits` reads it back
    end: str
    timezone: str
    where: str


class TripFacts(BaseModel):
    destination: str | None
    start_date: date | None
    end_date: date | None
    travelers: Travelers | None
    origin: str | None
    budget: Money | None
    no_flights: bool  # the trip is planned without flying (`set_travel_mode`)
    # What each search looks for, by kind (flights, lodging, activities); a kind that is
    # missing hasn't been asked yet, and its search refuses until it is.
    preferences: dict[str, JsonValue]
    declined: list[SlotName]  # optional questions the user skipped


class FormView(BaseModel):
    type: Literal["form"] = "form"
    slot: SlotName
    question: str


class ChoicesView(BaseModel):
    type: Literal["choices"] = "choices"
    slot: SlotName
    candidates: list[CandidateView]
    min_picks: int
    max_picks: int


class SuggestionView(BaseModel):
    type: Literal["suggestion"] = "suggestion"
    reason: str
    changes: list[str]


PendingView = Annotated[FormView | ChoicesView | SuggestionView, Field(discriminator="type")]


class PlannerContext(BaseModel):
    """The plan as the model sees it."""

    today: date
    trip: TripFacts
    items: list[ItemView]
    pending: PendingView | None  # what the user was last asked and hasn't answered
    searches_left: int  # paid searches this turn may still run
    problems: list[ProblemView]
    complete: bool  # no blocking problem is left


class Line(BaseModel):
    """One transcript message."""

    who: Literal["user", "planner"]
    text: str


class TurnBrief(BaseModel):
    """A turn's first user message."""

    conversation: list[Line]  # the latest messages, oldest first
    plan: PlannerContext
    # What the user just typed (also the conversation's last line); None when they used
    # the interface instead (a form, a pick, a button), which the conversation ends on.
    said: str | None = None
    focus: str | None = None  # the id of the problem the user asked to work on


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _place(place: Place | None) -> str | None:
    if place is None:
        return None
    return ", ".join(part for part in (place.name, place.country) if part)


def _money(money: Money) -> str:
    return f"{money.amount:,.2f}".removesuffix(".00") + f" {money.currency}"


def problem_views(problems: Sequence[Problem]) -> list[ProblemView]:
    return [
        ProblemView(
            id=problem.id,
            severity=problem.severity,
            message=problem.message,
            how=problem.fix.how,
            slot=problem.fix.slot,
        )
        for problem in problems
    ]


def candidate_view(ref: str, candidate: Candidate) -> CandidateView:
    return CandidateView(
        ref=ref,
        title=candidate.title,
        subtitle=candidate.subtitle,
        details=candidate.details,
        warnings=candidate.warnings,
        price=_money(candidate.price) if candidate.price is not None else None,
    )


def _item_view(number: int, item: ItineraryItem) -> ItemView:
    if isinstance(item, FlightItem | TransferItem):
        where = f"{item.from_place.name} to {item.to_place.name}"
    else:
        where = item.place.name
    zone = item.timezone
    if item.end_timezone is not None and item.end_timezone != item.timezone:
        zone = f"{item.timezone} to {item.end_timezone}"
    return ItemView(
        number=number,
        type=item.type,
        title=item.title,
        start=f"{item.start:%a %Y-%m-%dT%H:%M}",
        end=f"{item.end:%a %Y-%m-%dT%H:%M}",
        timezone=zone,
        where=where,
    )


def _preferences(plan: TravelPlan) -> dict[str, JsonValue]:
    # Read through the slots, so the flights' search token stays out of the prompt.
    stored: dict[str, JsonValue] = {}
    for name, slot in PREFERENCE_SLOTS.items():
        read = SLOTS[slot].read
        answer = read(plan) if read is not None else None
        if answer is not None:
            stored[name] = answer.model_dump(mode="json")
    return stored


def _pending(pending: Pending | None) -> PendingView | None:
    match pending:
        case AskInput():
            return FormView(slot=pending.slot, question=pending.prompt)
        case OfferChoice():
            return ChoicesView(
                slot=pending.slot,
                candidates=[candidate_view(c.id, c) for c in pending.candidates],
                min_picks=pending.min_picks,
                max_picks=pending.max_picks,
            )
        case ReviewProposal():
            return SuggestionView(
                reason=pending.prompt, changes=[change.summary for change in pending.changes]
            )
    return None


def planner_context(
    plan: TravelPlan, *, today: date, searches_left: int, asked: AskInput | None = None
) -> PlannerContext:
    """The plan as the model sees it. `asked` is the form the user's text came in under:
    typing instead of filling it in takes it off the plan's `pending` (the transcript no
    longer ends on it), but the text is most likely its answer."""
    return PlannerContext(
        today=today,
        trip=TripFacts(
            destination=_place(plan.destination),
            start_date=plan.start_date,
            end_date=plan.end_date,
            travelers=plan.travelers,
            origin=_place(plan.origin),
            budget=plan.budget,
            no_flights=plan.no_flights,
            preferences=_preferences(plan),
            declined=plan.preferences.declined,
        ),
        items=[
            _item_view(number, item) for number, item in enumerate(numbered_items(plan), start=1)
        ],
        pending=_pending(asked or plan.pending),
        searches_left=searches_left,
        problems=problem_views(plan.problems),
        complete=plan.complete,
    )


def conversation(messages: Sequence[tuple[str, str]]) -> list[Line]:
    """Transcript messages (role, content) as the lines the model reads. System notices
    (a failed turn) aren't part of the conversation."""
    return [
        Line(who="user" if role == "user" else "planner", text=content)
        for role, content in messages
        if role != "system" and content
    ]
