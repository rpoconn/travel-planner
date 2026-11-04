"""Planning engine schemas: the form fields a slot asks with, the candidates an offer
lists, the engine's next action, and the `TravelPlan` the API returns.

`TravelPlan` lives here, not in `plans`, because it carries the engine's assessment and
the dependency only runs one way: `planning` imports `plans`, never the reverse.
"""

import datetime as dt
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from features.itinerary.schemas import Gap
from features.plans.schemas import Issue, PlanSnapshot, PlanStage, SlotName
from shared.domain import Money, Place

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
SearchKind = Literal["flights_outbound", "flights_return", "lodging", "activities", "transfers"]


class CandidateChange(BaseModel):
    """The plan change picking a candidate makes: a write tool's name and arguments, run
    through the same validation as any other change (`chat.tools.check_change`)."""

    name: str  # e.g. "add_flight"
    arguments: dict[str, Any]
    summary: str  # imperative, e.g. 'Add flight "SFO → NRT" on 2027-05-01 11:00'


class Candidate(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str  # stable within its offer
    title: str
    subtitle: str = ""
    details: list[str] = Field(default_factory=list)
    price: Money | None = None
    source_url: str | None = None
    change: CandidateChange


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
    prefill: dict[str, Any] = Field(default_factory=dict)


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


class RunSearch(BaseModel):
    """Search for the slot's candidates (the "Find flights" / "Retry" button)."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["run_search"] = "run_search"
    kind: SearchKind
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


class Complete(BaseModel):
    """At review: nothing left to ask. Remaining issues are in the assessment."""

    type: Literal["complete"] = "complete"


NextAction = Annotated[
    AskInput | OfferChoice | RunSearch | AdvanceStage | Complete,
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
    value: dict[str, Any]


class SlotSkip(BaseModel):
    """Skip the pending input slot; only optional slots (the budget) can be skipped."""

    type: Literal["skip"] = "skip"
    slot: SlotName


PlanningInput = Annotated[SlotAnswer | SlotSkip, Field(discriminator="type")]


# ---------------------------------------------------------------------------
# Transcript payloads: the structured content a planning message carries, so the
# transcript re-renders forms, choices and answers after a reload
# ---------------------------------------------------------------------------


class AnswerRecord(BaseModel):
    """A user's answer to an input slot (message kind "input")."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    type: Literal["answer"] = "answer"
    slot: SlotName
    value: dict[str, Any] | None = None  # the validated answer; None when skipped
    skipped: bool = False


class StageRecord(BaseModel):
    """The plan moved to another stage (message kind "stage")."""

    type: Literal["stage"] = "stage"
    from_stage: PlanStage
    to_stage: PlanStage


# kind "expected_input" → AskInput, "choices" → OfferChoice, "input" → AnswerRecord,
# "stage" → StageRecord. Other kinds carry no payload.
MessagePayload = Annotated[
    AskInput | OfferChoice | AnswerRecord | StageRecord,
    Field(discriminator="type"),
]


class TravelPlan(PlanSnapshot):
    """The full plan as the API returns it: details, items, stage checklists, gaps, and
    the planning engine's assessment (built by `planning.service.snapshot`)."""

    assessment: PlanAssessment
