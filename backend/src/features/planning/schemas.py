"""Planning engine schemas: the form fields a slot asks with, the candidates an offer
lists, the engine's next action, and the `TravelPlan` the API returns.

`TravelPlan` lives here, not in `plans`, because it carries the engine's assessment and
the dependency only runs one way: `planning` imports `plans`, never the reverse.
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
    ItemStatus,
    ItemTitle,
    LodgingKind,
)
from features.plans.schemas import (
    DepartWindow,
    HotelClass,
    Issue,
    PlanSnapshot,
    PlanStage,
    ShortAnswer,
    SlotName,
)
from shared.domain import Money, Place
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
# Slot values: what a basics slot's form (or the extractor) produces. The preference
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
    stage: PlanStage
    plan_version: int  # the plan's version when offered; any later change makes it stale
    candidates: list[Candidate]
    status: OfferStatus
    query_hash: str
    gap: Gap | None
    nights: NightRange | None
    created_at: datetime
    decided_at: datetime | None


# ---------------------------------------------------------------------------
# Next action: what the engine wants from the user (or itself) next
# ---------------------------------------------------------------------------


class AskInput(BaseModel):
    """Show the slot's form. `prefill` holds values the plan already has."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["ask_input"] = "ask_input"
    slot: SlotName
    stage: PlanStage
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
    stage: PlanStage
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
    """Search for the slot's candidates (the "Find flights" / "Retry" button)."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["run_search"] = "run_search"
    slot: SlotName
    stage: PlanStage
    label: str
    nights: NightRange | None = None
    gap: Gap | None = None


class AdvanceStage(BaseModel):
    """The current stage is done. The turn pipeline acts on this at once, so a client only
    sees it on a stage the user went back to: it then offers to continue."""

    type: Literal["advance_stage"] = "advance_stage"
    to: PlanStage


class ReturnToStage(BaseModel):
    """A stage before the current one has a blocking issue again (an item was removed or
    changed since it was finished), so the plan goes back to fix it. The turn pipeline acts
    on this at once; a client only sees it after an edit outside a turn, and then offers to
    go back."""

    type: Literal["return_to_stage"] = "return_to_stage"
    to: PlanStage
    reason: str  # the first issue, for people: "a place to stay the night of May 2"


class Complete(BaseModel):
    """At review: nothing left to ask. Remaining issues are in the assessment."""

    type: Literal["complete"] = "complete"


NextAction = Annotated[
    AskInput | OfferChoice | RunSearch | AdvanceStage | ReturnToStage | Complete,
    Field(discriminator="type"),
]


class PlanAssessment(BaseModel):
    stage: PlanStage
    # Blocking issues of the stages reached so far, then timeline warnings.
    issues: list[Issue]
    next_action: NextAction
    can_advance: bool  # the current stage's checklist is complete


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
    "change_stage",
    "redo_search",
]

# An item's number in the itinerary list the model is shown (`commands.numbered_items`),
# so it never has to spell an id; the server maps it back.
ItemRef = Annotated[int, Field(ge=1, description="The item's number in the itinerary list")]


class _Command(BaseModel):
    # Unknown fields are a malformed command, not something to drop silently.
    model_config = ConfigDict(extra="forbid")


class ItemEdits(_Command):
    """The fields a command may change on an item. Costs, places and airports come from
    searches, so they aren't here."""

    title: ItemTitle | None = None
    start: datetime | None = Field(
        default=None, description="Local wall-clock start in the item's timezone"
    )
    end: datetime | None = Field(
        default=None, description="Local wall-clock end in the item's (end) timezone"
    )
    notes: ItemNotes | None = None
    status: ItemStatus | None = None


class RemoveItem(_Command):
    """Take an item off the trip. It is cancelled, not deleted, so it can come back."""

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


class ChangeStage(_Command):
    """Move the plan back, or skip flights. Moving on is the engine's job, never a
    command's."""

    type: Literal["change_stage"] = "change_stage"
    action: Literal["back", "go_to", "skip_flights"]
    stage: PlanStage | None = Field(default=None, description="go_to only: the earlier stage")


# How a command says how many stops a flight may make. A preference's `max_stops` uses
# None for "any number", but a command can't send null (`inline_schema` drops it), so the
# choices are words, mapped back in `planning.commands`.
StopsChoice = Literal["nonstop", "up_to_1", "up_to_2", "any"]


# The preferences a command can change: the `PlanPreferences` field each kind is stored in.
PreferenceSection = Literal["flights", "lodging", "activities"]


class PreferencesCommand(_Command):
    """Changes what one stage searches for: the fields given go over the stored
    preferences and the rest keep their value. The plan's version moves on, so choices on
    show for that stage are searched for again."""

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


# The searches whose choices `RedoSearch` can ask others for, by the stage that runs them.
# Transfers aren't here: their rules list every way across at once.
SearchName = Literal["flights", "lodging", "activities"]


class RedoSearch(_Command):
    """Look for other options than the choices on show, as "More options" does."""

    type: Literal["redo_search"] = "redo_search"
    search: SearchName = Field(description="Which choices to find others for")


# The commands under each base class, for code that reads their `type`.
BasicsChange = SetDates | SetTravelers | SetDestination | SetOrigin | SetBudget
PreferencesChange = SetFlightPreferences | SetLodgingPreferences | SetActivityPreferences

Command = Annotated[
    RemoveItem | UpdateItem | BasicsChange | PreferencesChange | ChangeStage | RedoSearch,
    Field(discriminator="type"),
]

CommandStatus = Literal["command", "ambiguous", "question", "unrelated"]


class CommandReply(BaseModel):
    """The command reader's reply (`extractor.read_commands`). Every command is checked
    against the plan afterwards; this only fixes the shape."""

    status: CommandStatus = Field(
        description=(
            "command: the message asks to change the trip; ambiguous: it does, but which "
            "item or what change isn't clear; question: it asks something instead; "
            "unrelated: anything else"
        )
    )
    commands: list[Command] = Field(
        default_factory=list, description="command only: the changes, in the order to make them"
    )
    clarification: str | None = Field(
        default=None, description="ambiguous only: one short question asking which"
    )


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


class StageRecord(BaseModel):
    """The plan moved to another stage (message kind "stage")."""

    type: Literal["stage"] = "stage"
    from_stage: PlanStage
    to_stage: PlanStage


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
    # Set instead when the words could mean more than one change: the question asked back.
    # The user's reply is read together with it (`turn._free_text`).
    clarification: str | None = None


# kind "expected_input" → AskInput, "choices" → OfferChoice, "input" → AnswerRecord,
# "pick" → PickRecord, "stage" → StageRecord, "changes" → ChangesRecord. Other kinds
# carry no payload.
MessagePayload = Annotated[
    AskInput | OfferChoice | AnswerRecord | PickRecord | StageRecord | ChangesRecord,
    Field(discriminator="type"),
]


class TravelPlan(PlanSnapshot):
    """The full plan as the API returns it: details, items, stage checklists, gaps, and
    the planning engine's assessment (built by `planning.service.snapshot`)."""

    assessment: PlanAssessment
