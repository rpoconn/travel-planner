"""The planner's stand-in under `LLM_PROVIDER=fake` (offline dev and the Playwright suite): a
small scripted policy that plays the model's part in the loop, so a trip can be planned
with no model. Test-only: it knows a handful of phrases and one way through a plan.

It reads what the model would (`context.TurnBrief`, then the tools' results) and replies
with one tool call or one line of text:

- First it reacts to what the user typed: "the second one" picks from the open choices;
  "yes" / "no" decides the open suggestion; an edit phrase ("remove the <words>", "we're
  driving", "make the hotel <class>") or a phrase that answers the open form ("lisbon",
  "two adults") becomes `apply_user_changes`; "what would you drop?" suggests removing the
  last thing to do, so a suggestion can be approved or rejected; any other question gets
  a fixed answer.
- Otherwise, and after a write, it works on the first blocking problem (or the one in
  `focus`): it asks the problem's form, asks the preferences a search needs, or searches
  and then shows what the search found.
- A tool that fails ends the turn with its reason.
"""

from datetime import date, timedelta
from typing import get_args

from pydantic import BaseModel, JsonValue, ValidationError

from features.planning.agent.context import (
    PREFERENCE_SLOTS,
    ChoicesView,
    FormView,
    PlannerContext,
    ProblemView,
    SuggestionView,
    TurnBrief,
)
from features.planning.agent.tool_models import (
    SEARCH_PREFERENCES,
    SEARCH_SLOTS,
    SearchKind,
    SearchResult,
    ToolFailure,
    WriteResult,
)
from features.planning.slots import SLOTS
from features.plans.schemas import HotelClass, SlotName
from shared.llm.fake import FakeRequest, text, tool_calls
from shared.llm.types import LLMMessage

OFFLINE_ANSWER = (
    "I'm running offline (LLM_PROVIDER=fake), so I can't answer questions right now. "
    "Tell me what to change, or carry on with the trip."
)
COMPLETE = "Your trip has everything it needs."
PICK_ABOVE = "Pick one of the options above, or tell me what you'd like instead."
NOTHING_TO_DROP = "There's nothing I'd drop."
# The phrase that makes the policy suggest a change of its own.
SUGGEST_PHRASE = "what would you drop"
SUGGEST_REASON = "A lighter trip: I'd drop this one."

_KINDS: dict[SlotName, SearchKind] = {slot: kind for kind, slot in SEARCH_SLOTS.items()}
_SECTIONS: dict[SlotName, str] = {slot: name for name, slot in PREFERENCE_SLOTS.items()}
_ORDINALS = ("first", "second", "third", "fourth", "fifth")
_STAY_WORDS = ("hotel", "stay")
_APPROVE = ("yes", "approve", "do it")
_REJECT = ("no", "reject", "no thanks")

Command = dict[str, JsonValue]

# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------


def _phrase(content: str) -> str:
    return content.strip().rstrip(".!?").casefold()


def _dates(start: date, nights: int) -> Command:
    end = start + timedelta(days=nights)
    return {"type": "set_dates", "start": start.isoformat(), "end": end.isoformat()}


def _answers(today: date) -> dict[SlotName, dict[str, Command]]:
    """Exact (case-folded) phrases that answer each slot's form, as the command each means."""
    month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return {
        SlotName.DESTINATION: {
            "lisbon": {
                "type": "set_destination",
                "name": "Lisbon",
                "country": "Portugal",
                "iata": "LIS",
            },
            "paris": {"type": "set_destination", "name": "Paris", "country": "France"},
            "tokyo": {"type": "set_destination", "name": "Tokyo", "country": "Japan"},
        },
        SlotName.DATES: {
            "next month for a week": _dates(month, 7),
            "a long weekend next month": _dates(month, 3),
            # In the past: the rules turn it down, and the turn ends on why.
            "last month": _dates(today.replace(day=1) - timedelta(days=20), 3),
        },
        SlotName.TRAVELERS: {
            "just me": {"type": "set_travelers", "adults": 1, "children": 0},
            "two adults": {"type": "set_travelers", "adults": 2, "children": 0},
            "two adults and a child": {"type": "set_travelers", "adults": 2, "children": 1},
        },
        SlotName.ORIGIN: {
            "philadelphia": {"type": "set_origin", "name": "Philadelphia", "iata": "PHL"},
            "new york": {"type": "set_origin", "name": "New York", "iata": "JFK"},
        },
        SlotName.BUDGET: {
            "5000 dollars": {"type": "set_budget", "amount": 5000, "currency": "USD"},
        },
        SlotName.FLIGHT_PREFERENCES: {
            "economy": {"type": "set_flight_preferences", "cabin": "economy"},
            "economy, nonstop": {
                "type": "set_flight_preferences",
                "cabin": "economy",
                "max_stops": "nonstop",
            },
        },
        SlotName.LODGING_PREFERENCES: {
            "a midrange hotel": {
                "type": "set_lodging_preferences",
                "hotel_class": "midrange",
                "kind": "hotel",
            },
        },
        SlotName.ACTIVITY_PREFERENCES: {
            "museums and food": {
                "type": "set_activity_preferences",
                "interests": ["museum", "food"],
            },
        },
    }


def _item_number(words: str, plan: PlannerContext) -> int | None:
    """The first item `words` names: "the hotel" is the first stay, anything else the
    first item whose title has the words."""
    for item in plan.items:
        named = item.type == "lodging" if words in _STAY_WORDS else words in item.title.casefold()
        if named:
            return item.number
    return None


def _edit(phrase: str, plan: PlannerContext) -> Command | None:
    """The command an edit phrase asks for, whatever the plan is waiting on."""
    if phrase == "we're driving":
        return {"type": "set_travel_mode", "no_flights": True}
    if phrase.startswith("remove "):
        number = _item_number(phrase.removeprefix("remove ").removeprefix("the "), plan)
        return None if number is None else {"type": "remove_item", "item": number}
    if phrase.startswith("make the hotel "):
        hotel_class = phrase.removeprefix("make the hotel ")
        if hotel_class in get_args(HotelClass):
            return {"type": "set_lodging_preferences", "hotel_class": hotel_class}
    return None


# ---------------------------------------------------------------------------
# Reacting to what the user typed
# ---------------------------------------------------------------------------


def _call(name: str, **arguments: JsonValue) -> LLMMessage:
    return tool_calls((name, arguments))


def _suggestion(plan: PlannerContext) -> LLMMessage:
    activities = [item for item in plan.items if item.type == "activity"]
    if not activities:
        return text(NOTHING_TO_DROP)
    change: Command = {"type": "remove_item", "item": activities[-1].number}
    return _call("suggest_changes", reason=SUGGEST_REASON, changes=[change])


def _react(said: str, plan: PlannerContext, offered: frozenset[str]) -> LLMMessage | None:
    """The reply to the user's text, or None when it says nothing the policy knows."""
    phrase = _phrase(said)
    pending = plan.pending
    ordinal = phrase.removeprefix("the ").removesuffix(" one")
    if isinstance(pending, ChoicesView) and ordinal in _ORDINALS and "pick_choice" in offered:
        return _call("pick_choice", candidates=[str(_ORDINALS.index(ordinal) + 1)])
    if isinstance(pending, SuggestionView) and "decide_proposal" in offered:
        if phrase in _APPROVE:
            return _call("decide_proposal", decision="approve")
        if phrase in _REJECT:
            return _call("decide_proposal", decision="reject")
    command = _edit(phrase, plan)
    if command is None and isinstance(pending, FormView):
        command = _answers(plan.today).get(pending.slot, {}).get(phrase)
    if command is not None and "apply_user_changes" in offered:
        return _call("apply_user_changes", quote=said, changes=[command])
    if phrase == SUGGEST_PHRASE:
        return _suggestion(plan)
    if said.strip().endswith("?"):
        return text(OFFLINE_ANSWER)
    return None


# ---------------------------------------------------------------------------
# Working on the next problem
# ---------------------------------------------------------------------------


def _ask(slot: SlotName) -> LLMMessage:
    return _call("ask_user", question=SLOTS[slot].prompt, slot=slot.value)


def _search(problem: ProblemView, slot: SlotName, plan: PlannerContext) -> LLMMessage:
    kind = _KINDS[slot]
    preferences = SEARCH_PREFERENCES.get(kind)
    if preferences is not None and _SECTIONS[preferences] not in plan.trip.preferences:
        return _ask(preferences)
    if isinstance(plan.pending, ChoicesView) and plan.pending.slot == slot:
        return text(PICK_ABOVE)
    if kind != "transfer" and plan.searches_left == 0:
        return text(f"Next up: {problem.message}. Say the word and I'll look.")
    if kind in ("stay", "transfer"):
        return _call("search", kind=kind, problem=problem.id)
    return _call("search", kind=kind)


def _advance(plan: PlannerContext, focus: str | None) -> LLMMessage:
    blocking = [problem for problem in plan.problems if problem.severity == "blocking"]
    if not blocking:
        return text(COMPLETE)
    problem = next((p for p in blocking if p.id == focus), blocking[0])
    if problem.slot is not None and problem.how == "ask":
        return _ask(problem.slot)
    if problem.slot is not None and problem.how == "search":
        return _search(problem, problem.slot, plan)
    return text(f"The trip still needs {problem.message}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _read[Model: BaseModel](model: type[Model], content: str) -> Model | None:
    try:
        return model.model_validate_json(content)
    except ValidationError:
        return None


def agent_reply(request: FakeRequest) -> LLMMessage:
    """The policy's reply to one of the loop's requests."""
    brief = next(
        (
            read
            for message in request.messages
            if message.role == "user" and (read := _read(TurnBrief, message.content)) is not None
        ),
        None,
    )
    if brief is None:
        return text(OFFLINE_ANSWER)
    offered = frozenset(tool.name for tool in request.tools)
    last = request.messages[-1]
    if last.role != "tool":
        reply = _react(brief.said, brief.plan, offered) if brief.said is not None else None
        return reply or _advance(brief.plan, brief.focus)

    failure = _read(ToolFailure, last.content)
    if failure is not None:
        return text(failure.error)
    found = _read(SearchResult, last.content)
    if found is not None:
        return _call("present_choices", search=found.search)
    written = _read(WriteResult, last.content)
    if written is not None and not written.changed:
        return text(written.summary)
    if written is not None:
        return _advance(written.plan, None)
    return text(OFFLINE_ANSWER)
