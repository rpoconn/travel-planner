"""The planner's tools as the model sees them: each one's arguments, its result, and the
`ToolSpec` built from them. `planning.agent.tools` runs them.

| Tool                 | Does                                              | Ends the turn |
|----------------------|---------------------------------------------------|---------------|
| `validate_plan`      | The plan's problems, and whether it is complete   | no            |
| `search`             | Finds options for a pick slot                     | no            |
| `ask_user`           | A question, with a slot's form under it or not    | yes           |
| `present_choices`    | Puts a search's options to the user to pick from  | yes           |
| `suggest_changes`    | Checks changes and stores them for approval       | yes           |
| `apply_user_changes` | Writes changes the user asked for in their words  | no            |
| `pick_choice`        | The user picked from the open choices in words    | no            |
| `decide_proposal`    | The user approved or rejected the suggestion      | no            |

The last three write to the plan without a card to approve, so they are only offered on a
turn the user typed (`tools.available`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from features.planning.agent.context import CandidateView, PlannerContext, ProblemView
from features.planning.schemas import MAX_COMMANDS, AgentCommand
from features.plans.schemas import SlotName
from shared.llm.schema import inline_schema
from shared.llm.types import ToolSpec

ToolName = Literal[
    "validate_plan",
    "search",
    "ask_user",
    "present_choices",
    "suggest_changes",
    "apply_user_changes",
    "pick_choice",
    "decide_proposal",
]

# ---------------------------------------------------------------------------
# Searches and forms, as the tools name them
# ---------------------------------------------------------------------------

SearchKind = Literal["flight_out", "flight_home", "stay", "activities", "transfer"]

SEARCH_SLOTS: dict[SearchKind, SlotName] = {
    "flight_out": SlotName.FLIGHT_OUTBOUND,
    "flight_home": SlotName.FLIGHT_RETURN,
    "stay": SlotName.LODGING_STAY,
    "activities": SlotName.ACTIVITIES,
    "transfer": SlotName.TRANSFER,
}

# The preferences a search reads, which have to be asked before it runs. A transfer reads
# none: it comes from rules, not a paid search.
SEARCH_PREFERENCES: dict[SearchKind, SlotName] = {
    "flight_out": SlotName.FLIGHT_PREFERENCES,
    "flight_home": SlotName.FLIGHT_PREFERENCES,
    "stay": SlotName.LODGING_PREFERENCES,
    "activities": SlotName.ACTIVITY_PREFERENCES,
}

# The slots `ask_user` can show a form for: every input slot (`slots.SLOTS`; a test keeps
# the two in step).
InputSlot = Literal[
    "destination",
    "dates",
    "travelers",
    "origin",
    "budget",
    "flight_preferences",
    "lodging_preferences",
    "activity_preferences",
]

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


class _Args(BaseModel):
    # An unknown field is a malformed call the model should repair, not something to drop.
    model_config = ConfigDict(extra="forbid")


class ValidatePlanArgs(_Args):
    pass


class SearchArgs(_Args):
    kind: SearchKind
    problem: str | None = Field(
        default=None,
        description=(
            "stay and transfer only: the id of the problem to fix (the nights without a "
            "place to stay, or the change of place without a ride)"
        ),
    )
    more: bool = Field(
        default=False,
        description="true: other options than the ones already shown for this search",
    )


class AskUserArgs(_Args):
    question: ShortText = Field(description="One short question, as the user will read it")
    slot: InputSlot | None = Field(
        default=None,
        description=(
            "Show this slot's form under the question. Leave out for a question no form "
            "fits; the user answers in their own words either way"
        ),
    )


class PresentChoicesArgs(_Args):
    search: str = Field(description='The search whose options to show, e.g. "a"')


class SuggestChangesArgs(_Args):
    reason: ShortText = Field(description="Why you suggest this, in one sentence for the user")
    changes: list[AgentCommand] = Field(min_length=1, max_length=MAX_COMMANDS)


class ApplyUserChangesArgs(_Args):
    quote: ShortText = Field(
        description="The words of the user's latest message that ask for these changes, verbatim"
    )
    changes: list[AgentCommand] = Field(min_length=1, max_length=MAX_COMMANDS)


class PickChoiceArgs(_Args):
    candidates: list[str] = Field(
        min_length=1, description="The refs of the open choices the user picked, in their order"
    )


class DecideProposalArgs(_Args):
    decision: Literal["approve", "reject"]


# ---------------------------------------------------------------------------
# Results: what the model reads back, as JSON
# ---------------------------------------------------------------------------


class ToolFailure(BaseModel):
    """The call did nothing, and why. The model repairs the call or tells the user."""

    model_config = ConfigDict(extra="forbid")

    error: str


class ValidationResult(BaseModel):
    problems: list[ProblemView]
    complete: bool


class SearchResult(BaseModel):
    search: str  # names the search for `present_choices`: "a", "b"...
    candidates: list[CandidateView]  # their refs ("a1", "a2") are what `add_candidate` names
    searches_left: int


class WriteResult(BaseModel):
    """What a write did, and the plan it left: items are renumbered by any change."""

    changed: bool
    summary: str
    plan: PlannerContext


class Shown(BaseModel):
    """A turn-ending tool put something to the user; the turn is over."""

    shown: Literal["question", "form", "choices", "suggestion"]


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    arguments: type[BaseModel]
    description: str
    ends_turn: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=inline_schema(self.arguments),
        )


_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="validate_plan",
        arguments=ValidatePlanArgs,
        description=(
            "Check the plan as it stands: everything still wrong with it, and whether it is "
            "complete. Call it after a change to see what is left."
        ),
    ),
    ToolDefinition(
        name="search",
        arguments=SearchArgs,
        description=(
            "Find options for the trip: flights out or home, a place to stay for a run of "
            "nights, things to do, or a ride across a change of place. Returns the options; "
            "nothing is added or shown until you call present_choices or suggest_changes. "
            "Searches other than transfer cost money: at most 2 a turn."
        ),
    ),
    ToolDefinition(
        name="ask_user",
        arguments=AskUserArgs,
        description="Ask the user one question and wait for their answer. Ends your turn.",
        ends_turn=True,
    ),
    ToolDefinition(
        name="present_choices",
        arguments=PresentChoicesArgs,
        description=(
            "Show a search's options for the user to pick from; what they pick is added to "
            "the trip. Ends your turn."
        ),
        ends_turn=True,
    ),
    ToolDefinition(
        name="suggest_changes",
        arguments=SuggestChangesArgs,
        description=(
            "Suggest changes of your own. They are checked against the trip's rules and "
            "shown as a card the user approves or rejects; nothing changes until they "
            "approve. Ends your turn."
        ),
        ends_turn=True,
    ),
    ToolDefinition(
        name="apply_user_changes",
        arguments=ApplyUserChangesArgs,
        description=(
            "Make changes the user's latest message asks for, at once. Only for what they "
            "asked in so many words: quote them. Anything else goes through suggest_changes."
        ),
    ),
    ToolDefinition(
        name="pick_choice",
        arguments=PickChoiceArgs,
        description=(
            'The user\'s latest message picks from the open choices ("the second one", '
            '"the TAP flight"): add what they picked.'
        ),
    ),
    ToolDefinition(
        name="decide_proposal",
        arguments=DecideProposalArgs,
        description=(
            "The user's latest message approves or rejects the open suggestion in words: "
            "record their decision (approving makes its changes)."
        ),
    ),
)

TOOLS: dict[str, ToolDefinition] = {definition.name: definition for definition in _DEFINITIONS}


def tool_specs(names: Sequence[ToolName]) -> list[ToolSpec]:
    return [TOOLS[name].spec for name in names]
