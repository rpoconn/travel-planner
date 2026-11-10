"""Planning schemas: the form fields a slot asks with, the candidates an offer lists, the
plan's problems, the commands that change it, and the `TravelPlan` the API returns.

`TravelPlan` lives here, not in `plans`, because it carries the plan's problems and what it
is waiting on, and the dependency only runs one way: `planning` imports `plans`, never the
reverse.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from features.itinerary.schemas import (
    ActivityCategory,
    Gap,
    ItemNotes,
    ItemTitle,
    LodgingKind,
)
from features.plans.schemas import (
    DepartWindow,
    HotelClass,
    PlanSnapshot,
    ShortAnswer,
    SlotName,
)
from shared.domain import IssueCode, Money, Place
from shared.flights.types import FlightCabin

# ---------------------------------------------------------------------------
# Form fields
# ---------------------------------------------------------------------------

# How the UI draws a field and what JSON it sends back for it:
# date_range → {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}, integer → int,
# money → {"amount", "currency"}, text → str, place → a Place object,
# choice → one option's value, multi_choice → a list of option values.
FieldKind = Literal["date_range", "integer", "money", "text", "place", "choice", "multi_choice"]


class FieldOption(BaseModel):
    value: str | int | None
    label: str


class FieldSpec(BaseModel):
    """One input on a slot's form. `name` is the key in the slot's value."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    name: str
    label: str
    kind: FieldKind
    required: bool = True
    options: list[FieldOption] = Field(default_factory=list)  # choice / multi_choice
    min: int | None = None  # integer: lowest value; multi_choice: fewest picks
    max: int | None = None  # integer: highest value; multi_choice: most picks
    placeholder: str = ""


class DateRange(BaseModel):
    start: dt.date
    end: dt.date


class NightRange(BaseModel):
    """Consecutive nights without lodging: check in on `check_in`, out on `check_out`."""

    check_in: dt.date
    check_out: dt.date


# ---------------------------------------------------------------------------
# Slot values: what a basics slot's form (or a command) produces. The preference
# slots use `plans.schemas.*Preferences*` directly; pick slots take candidate ids.
# ---------------------------------------------------------------------------


class DestinationAnswer(BaseModel):
    destination: Place


class DatesAnswer(BaseModel):
    dates: DateRange

    @model_validator(mode="after")
    def _in_order(self) -> Self:
        if self.dates.end < self.dates.start:
            raise ValueError("The trip has to end on or after the day it starts")
        return self


class OriginAnswer(BaseModel):
    origin: Place


class BudgetAnswer(BaseModel):
    budget: Money

    @model_validator(mode="after")
    def _positive(self) -> Self:
        # 0 means "no budget" on a plan; someone who has none skips the slot instead.
        if self.budget.amount <= 0:
            raise ValueError("A budget has to be more than 0; skip it if you don't have one")
        return self


# ---------------------------------------------------------------------------
# Offers: a persisted list of candidates for a pick slot
# ---------------------------------------------------------------------------

OfferStatus = Literal["open", "picked", "dismissed"]


class CandidateChange(BaseModel):
    """The plan change picking a candidate makes: a write tool's name and arguments, run
    through the same validation as any other change (`chat.tools.check_change`)."""

    name: str  # e.g. "add_flight"
    arguments: dict[str, JsonValue]
    summary: str  # imperative, e.g. 'Add flight "SFO → NRT" on 2027-05-01 11:00'


class Candidate(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str  # stable within its offer: "1", "2"... so "the second one" maps to an id
    title: str
    subtitle: str = ""
    details: list[str] = Field(default_factory=list)
    # How it falls short of what the user asked for, e.g. "1 stop, not nonstop".
    warnings: list[str] = Field(default_factory=list)
    price: Money | None = None
    source_url: str | None = None
    # Applied all-or-nothing, in order. Usually one; a round trip's flight home also
    # moves the whole fare onto the flight out (an update, then the add).
    changes: list[CandidateChange] = Field(min_length=1)
    # The provider's token for the search that follows picking this one: a round trip's
    # flight out lists its flights home with it.
    follow_up_token: str | None = None


class Offer(BaseModel):
    """An offer as stored (`planning.models.PlanningOffer`)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    slot: SlotName
    plan_version: int  # the plan's version when offered; any later change makes it stale
    candidates: list[Candidate]
    status: OfferStatus
    query_hash: str
    gap: Gap | None
    nights: NightRange | None
    created_at: datetime
    decided_at: datetime | None


# ---------------------------------------------------------------------------
# What the user is shown to answer, and the search behind a list of choices
# ---------------------------------------------------------------------------


class AskInput(BaseModel):
    """Show the slot's form. `prefill` holds values the plan already has."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["ask_input"] = "ask_input"
    slot: SlotName
    prompt: str
    fields: list[FieldSpec]
    optional: bool = False
    prefill: dict[str, JsonValue] = Field(default_factory=dict)


class OfferChoice(BaseModel):
    """Show the open offer's candidates; picking is the approval."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["offer_choice"] = "offer_choice"
    offer_id: UUID
    slot: SlotName
    prompt: str
    candidates: list[Candidate]
    min_picks: int = 1
    max_picks: int = 1
    gap: Gap | None = None
    nights: NightRange | None = None
    # Whether "More options" can search again for others (`POST /continue` with
    # `refresh`). Transfers come from rules that list every way across at once.
    can_refresh: bool = False


class RunSearch(BaseModel):
    """A search for a pick slot's candidates, as the search runners take it
    (`planning.searches.run_search`)."""

    slot: SlotName
    label: str  # shown while it runs, e.g. "Finding flights out"
    nights: NightRange | None = None
    gap: Gap | None = None


# ---------------------------------------------------------------------------
# Problems: what the validation engine reports (`planning.validation.validate`)
# ---------------------------------------------------------------------------


ProblemSeverity = Literal["blocking", "warning"]


class ProblemFix(BaseModel):
    """How a problem is usually fixed. A hint for whoever drives the plan, not a rule."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    # ask: the user answers input slot `slot`; search: candidates for pick slot `slot` are
    # searched for and picked from; edit: the items the problem names are changed or removed.
    how: Literal["ask", "search", "edit"]
    slot: SlotName | None = None


class Problem(BaseModel):
    """One thing wrong with the plan. A plan with no blocking problem is complete."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    # Names the problem while the plan doesn't change: "missing_return",
    # "uncovered_night:2027-05-02", "missing_transfer:<item before>:<item after>".
    id: str
    code: IssueCode
    severity: ProblemSeverity
    message: str  # for people, e.g. "a place to stay May 2 to May 5"
    # `dt.date`: a field named `date` would shadow the type in later annotations.
    date: dt.date | None = None
    item_ids: list[UUID] = Field(default_factory=list)
    gap: Gap | None = None  # missing_transfer: the change of place to bridge
    nights: NightRange | None = None  # uncovered_night: the run of nights one stay covers
    fix: ProblemFix


# ---------------------------------------------------------------------------
# Inputs: what the planning flow's forms send (`POST /plans/{id}/planning/inputs`)
# ---------------------------------------------------------------------------


class SlotAnswer(BaseModel):
    """The pending input slot's form, filled in. `value` is checked against the slot's
    value model (`planning.answers.parse_answer`), so its shape depends on `slot`."""

    type: Literal["answer"] = "answer"
    slot: SlotName
    value: dict[str, JsonValue]


class SlotSkip(BaseModel):
    """Skip the pending input slot; only optional slots (the budget) can be skipped."""

    type: Literal["skip"] = "skip"
    slot: SlotName


class ChoicePick(BaseModel):
    """Pick from the open offer: picking is the approval. The ids must be the offer's, and
    as many as its pick slot takes."""

    type: Literal["pick"] = "pick"
    offer_id: UUID
    candidate_ids: list[str] = Field(min_length=1)


PlanningInput = Annotated[SlotAnswer | SlotSkip | ChoicePick, Field(discriminator="type")]


# ---------------------------------------------------------------------------
# Commands: plan edits read from the user's own words ("remove the Friday dinner"). The
# model only fills these in; `planning.commands` checks them against the plan's rules and
# writes them. The union is closed: anything it can't say is not an edit.
#
# Claude's structured outputs allow at most 24 optional fields across the whole schema
# (`shared.llm.anthropic_client.schema_size`, checked in a test), so each command changes one
# thing, its fields are required wherever a value is always known, and places carry only
# what a search uses.
# ---------------------------------------------------------------------------

MAX_COMMANDS = 5

CommandName = Literal[
    "remove_item",
    "update_item",
    "set_dates",
    "set_travelers",
    "set_destination",
    "set_origin",
    "set_budget",
    "set_flight_preferences",
    "set_lodging_preferences",
    "set_activity_preferences",
    "set_travel_mode",
    "add_candidate",
]

# An item's number in the itinerary list the model is shown (`commands.numbered_items`),
# so it never has to spell an id; the server maps it back.
ItemRef = Annotated[int, Field(ge=1, description="The item's number in the itinerary list")]


class _Command(BaseModel):
    # Unknown fields are a malformed command, not something to drop silently.
    model_config = ConfigDict(extra="forbid")


class ItemEdits(_Command):
    """The fields a command may change on an item. Costs, places and airports come from
    searches, so they aren't here. Taking an item off the trip is `RemoveItem`, so there
    is no cancelled status to set."""

    title: ItemTitle | None = None
    start: datetime | None = Field(
        default=None, description="Local wall-clock start in the item's timezone"
    )
    end: datetime | None = Field(
        default=None, description="Local wall-clock end in the item's (end) timezone"
    )
    notes: ItemNotes | None = None
    status: Literal["proposed", "confirmed"] | None = None


class RemoveItem(_Command):
    """Take an item off the trip: it is deleted, with the transfers to and from it."""

    type: Literal["remove_item"] = "remove_item"
    item: ItemRef


class UpdateItem(_Command):
    type: Literal["update_item"] = "update_item"
    item: ItemRef
    changes: ItemEdits


# The basics a command can change, each with the input slot whose rules it follows.
BasicName = Literal["dates", "travelers", "destination", "origin", "budget"]
BASICS_FIELDS: dict[BasicName, SlotName] = {
    "dates": SlotName.DATES,
    "travelers": SlotName.TRAVELERS,
    "destination": SlotName.DESTINATION,
    "origin": SlotName.ORIGIN,
    "budget": SlotName.BUDGET,
}


class BasicsCommand(_Command):
    """Changes one of the trip's basics. Its fields are the slot's answer (wrapped in
    `basic`, except travelers), so `planning.commands` checks it with the form's rules."""

    # Declared here so `type` is the first key of every subclass, whichever fields it
    # inherits: Claude's structured outputs write keys in schema order, so a command whose
    # tag came after its fields could never be chosen once the reply starts with "type".
    type: str
    basic: ClassVar[BasicName]


class SetDates(BasicsCommand):
    """Change the trip's dates."""

    type: Literal["set_dates"] = "set_dates"
    basic: ClassVar[BasicName] = "dates"
    start: dt.date
    end: dt.date


class SetTravelers(BasicsCommand):
    """Change who is going."""

    type: Literal["set_travelers"] = "set_travelers"
    basic: ClassVar[BasicName] = "travelers"
    adults: int
    children: int = Field(description="0 when there are none")


class _PlaceCommand(BasicsCommand):
    # Only what the flight and stay searches read: the city's name, and its airport code
    # and country when known. A command never sends coordinates or an address.
    name: str = Field(description="The city")
    country: str | None = None
    iata: str | None = Field(
        default=None, pattern=r"^[A-Z]{3}$", description="The city's main 3-letter airport code"
    )


class SetDestination(_PlaceCommand):
    """Change where the trip goes."""

    type: Literal["set_destination"] = "set_destination"
    basic: ClassVar[BasicName] = "destination"


class SetOrigin(_PlaceCommand):
    """Change where the trip starts from."""

    type: Literal["set_origin"] = "set_origin"
    basic: ClassVar[BasicName] = "origin"


# An amount and its currency as a command gives them: both required (the stored `Money`
# defaults the currency, which would make it one more optional field).
CommandAmount = Annotated[Decimal, Field(description="A plain number such as 1380.50")]
CommandCurrency = Annotated[
    str, Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code: USD, EUR, JPY...")
]


class CommandMoney(_Command):
    amount: CommandAmount
    currency: CommandCurrency


class SetBudget(BasicsCommand):
    """Change the trip's total budget."""

    type: Literal["set_budget"] = "set_budget"
    basic: ClassVar[BasicName] = "budget"
    amount: CommandAmount
    currency: CommandCurrency


# How a command says how many stops a flight may make. A preference's `max_stops` uses
# None for "any number", but a command can't send null (`inline_schema` drops it), so the
# choices are words, mapped back in `planning.commands`.
StopsChoice = Literal["nonstop", "up_to_1", "up_to_2", "any"]


# The preferences a command can change: the `PlanPreferences` field each kind is stored in.
PreferenceSection = Literal["flights", "lodging", "activities"]


class PreferencesCommand(_Command):
    """Changes what one kind of search looks for: the fields given go over the stored
    preferences and the rest keep their value. The plan's version moves on, so choices on
    show are searched for again."""

    section: ClassVar[PreferenceSection]


class SetFlightPreferences(PreferencesCommand):
    """Change what the flight search looks for; fields left out keep their value."""

    type: Literal["set_flight_preferences"] = "set_flight_preferences"
    section: ClassVar[PreferenceSection] = "flights"
    cabin: FlightCabin | None = None
    max_stops: StopsChoice | None = None
    depart_window: DepartWindow | None = None


class SetLodgingPreferences(PreferencesCommand):
    """Change what the stay search looks for; fields left out keep their value."""

    type: Literal["set_lodging_preferences"] = "set_lodging_preferences"
    section: ClassVar[PreferenceSection] = "lodging"
    hotel_class: HotelClass | None = None
    kind: LodgingKind | None = None
    nightly_budget: CommandMoney | None = None
    neighborhood: ShortAnswer | None = None


class SetActivityPreferences(PreferencesCommand):
    """Change what the activity search looks for; fields left out keep their value."""

    type: Literal["set_activity_preferences"] = "set_activity_preferences"
    section: ClassVar[PreferenceSection] = "activities"
    interests: list[ActivityCategory] | None = Field(
        default=None, description="The whole new list of interests, up to 5"
    )
    dietary: ShortAnswer | None = None


class SetTravelMode(_Command):
    """Say whether the trip has flights. Without them (driving, a train) the plan asks for
    no flight out or home."""

    type: Literal["set_travel_mode"] = "set_travel_mode"
    no_flights: bool = Field(
        description="true: getting there another way than flying; false: flights are wanted"
    )


class AddCandidate(_Command):
    """Add one option a search found. Items are never written any other way, so their
    places, times and costs are the search's, not the model's."""

    type: Literal["add_candidate"] = "add_candidate"
    candidate: str = Field(description="The option's ref, as the search result lists it")


# The commands under each base class, for code that reads their `type`.
BasicsChange = SetDates | SetTravelers | SetDestination | SetOrigin | SetBudget
PreferencesChange = SetFlightPreferences | SetLodgingPreferences | SetActivityPreferences

# Every command there is. The model never moves the plan along or redoes a search by
# command (it runs searches itself); it can add what a search found and say the trip has
# no flights.
AgentCommand = Annotated[
    RemoveItem | UpdateItem | BasicsChange | PreferencesChange | SetTravelMode | AddCandidate,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Transcript payloads: the structured content a planning message carries, so the
# transcript re-renders forms, choices and answers after a reload
# ---------------------------------------------------------------------------


class AnswerRecord(BaseModel):
    """A user's answer to an input slot (message kind "input")."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["answer"] = "answer"
    slot: SlotName
    value: dict[str, JsonValue] | None = None  # the validated answer; None when skipped
    skipped: bool = False


class PickRecord(BaseModel):
    """What the user picked from an offer (message kind "pick")."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["pick"] = "pick"
    offer_id: UUID
    slot: SlotName
    candidate_ids: list[str]
    titles: list[str]  # the picked candidates' titles, in the order picked


class AppliedChange(BaseModel):
    """One command that was written, as the transcript shows it."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    name: CommandName
    summary: str  # past tense, e.g. 'Removed “Dinner at Ramiro”'
    item_id: UUID | None = None  # the item an item command changed


class RejectedChange(BaseModel):
    """One command the rules turned down, and the rule it broke."""

    summary: str  # what was asked, e.g. 'Remove “Dinner at Ramiro”'
    reason: str  # for people, e.g. "end must be at or after start"


class ChangesRecord(BaseModel):
    """What a message's commands did (message kind "changes"). Commands are written all or
    nothing, so `rejected` is empty when anything was applied, unless the plan changed
    between the check and the write."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["changes"] = "changes"
    applied: list[AppliedChange] = Field(default_factory=list)
    rejected: list[RejectedChange] = Field(default_factory=list)
    # What the applied changes left to fix, in words, each with how: a flight, a stay or
    # the activities the plan no longer has ("The trip needs a flight home now. Pick
    # another flight, …"), else the rides they left missing (a removal took the taxi on to
    # dinner): "Nothing gets you from Hotel Okura to Sushi Dai now. …"
    left_open: list[str] = Field(default_factory=list)
    # Set instead when the words could mean more than one change: the question asked back.
    clarification: str | None = None


# kind "expected_input" → AskInput, "choices" → OfferChoice, "input" → AnswerRecord,
# "pick" → PickRecord, "changes" → ChangesRecord. Other kinds carry no payload.
MessagePayload = Annotated[
    AskInput | OfferChoice | AnswerRecord | PickRecord | ChangesRecord,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Proposals: changes the planner suggests on its own, waiting for Approve / Reject
# (`planning.proposals`). Changes the user asked for in words are written at once instead.
# ---------------------------------------------------------------------------

# pending: waiting for the user. approved / rejected: their decision. superseded: a newer
# proposal replaced it, or the plan changed under it. failed: approved, but a change no
# longer fit the plan, so nothing was written.
ProposalStatus = Literal["pending", "approved", "rejected", "superseded", "failed"]


class ProposalChange(BaseModel):
    """One change of a proposal, as it passed the dry run: the command, how the card words
    it, and for an `add_candidate` the candidate itself, so approving needs no search."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    summary: str  # imperative, e.g. 'Remove “Dinner at Ramiro” and “Taxi there”'
    command: AgentCommand
    candidate: Candidate | None = None


class Proposal(BaseModel):
    """A proposal as stored (`planning.models.PlanningProposal`)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    status: ProposalStatus
    reason: str  # why the planner suggests it, in a sentence
    changes: list[ProposalChange]  # written in order, all or nothing
    # The plan's version when suggested. Commands point at items by number, so any later
    # change to the plan makes the proposal stale.
    plan_version: int
    created_at: datetime
    decided_at: datetime | None


class SuggestedChange(BaseModel):
    """One row of a proposal card."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    name: CommandName
    summary: str
    candidate: Candidate | None = None  # add_candidate: what would be added


class ReviewProposal(BaseModel):
    """Show the pending proposal's changes with Approve and Reject."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["review_proposal"] = "review_proposal"
    proposal_id: UUID
    prompt: str  # the proposal's reason
    changes: list[SuggestedChange]


# What the plan is waiting on the user for, if anything: a form, choices to pick from, or
# a proposal to decide (`planning.pending`). Free text always works too.
Pending = Annotated[AskInput | OfferChoice | ReviewProposal, Field(discriminator="type")]


class TravelPlan(PlanSnapshot):
    """The full plan as the API returns it: details, items, gaps, the validation engine's
    problems and what it is waiting on the user for (built by `planning.service.snapshot`)."""

    # Everything wrong with the plan: blocking problems, then warnings
    # (`planning.validation.validate`).
    problems: list[Problem]
    complete: bool  # no blocking problem is left
    pending: Pending | None
