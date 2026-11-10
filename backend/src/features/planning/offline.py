"""Canned model replies for the planning flow under `LLM_PROVIDER=fake` (offline dev and the
Playwright suite), so free text works with no model.

- Extraction requests are recognised by their `format` schema: the fields of its `value`
  name the slot. The latest user message is looked up, case-folded, in a small phrase
  table for that slot; a match is answered, text ending in "?" is a question, and anything
  else is unrelated.
- Pick requests (`extractor.PICK_SCHEMA`) read "the first one", "the second one"... as
  that option's id; the ids are positions ("1", "2"), as the searches number them.
- Either reader says "command" for an edit phrase: "remove the <words>", "go back",
  "go back to <stage>", "skip flights", "show me other <hotels / flights / things to do /
  options>", "make the hotel <class>". The command reader (`extractor.COMMAND_SCHEMA`)
  then turns it into a command, finding the item in the numbered itinerary its prompt
  shows: the first one whose title has the words ("the hotel" or "the stay"
  is the first stay). "Other options" are the current stage's, as the prompt names it.
- Question requests (`prompts.QA_INTRO`), and anything else, get a fixed answer; a
  structured request for an unknown schema is "unrelated", which fails closed to a nudge.
- A request with tools comes from the planner's loop (`planning.agent.loop`), which has its
  own scripted policy (`planning.agent.offline`). That policy replaces the rest of this
  module once the stage flow is gone.
"""

import re
from datetime import date, timedelta
from typing import get_args

from pydantic import JsonValue

from features.planning.agent.offline import agent_reply
from features.planning.schemas import SearchName
from features.planning.slots import SLOTS
from features.plans.schemas import HotelClass, PlanStage, SlotName
from features.plans.stages import STAGE_LABELS
from shared.llm.fake import FakeRequest, json_reply, text
from shared.llm.types import LLMMessage

OFFLINE_ANSWER = (
    "I'm running offline (LLM_PROVIDER=fake), so I can't answer questions right now. "
    "The form below keeps the planning going."
)


# ---------------------------------------------------------------------------
# Phrase table
# ---------------------------------------------------------------------------


def _next_month(today: date) -> date:
    return (today.replace(day=1) + timedelta(days=32)).replace(day=1)


def _dates(start: date, nights: int) -> dict[str, JsonValue]:
    end = start + timedelta(days=nights)
    return {"dates": {"start": start.isoformat(), "end": end.isoformat()}}


def _phrases(today: date) -> dict[SlotName, dict[str, dict[str, JsonValue]]]:
    """Exact (case-folded) phrases each slot understands, and the value each one means."""
    month = _next_month(today)
    return {
        SlotName.DESTINATION: {
            "lisbon": {"destination": {"name": "Lisbon", "city": "Lisbon", "country": "Portugal"}},
            "paris": {"destination": {"name": "Paris", "city": "Paris", "country": "France"}},
            "tokyo": {"destination": {"name": "Tokyo", "city": "Tokyo", "country": "Japan"}},
        },
        SlotName.DATES: {
            "next month for a week": _dates(month, 7),
            "a long weekend next month": _dates(month, 3),
            # Answered, but in the past: shows the "that doesn't work" note.
            "last month": _dates(today.replace(day=1) - timedelta(days=20), 3),
        },
        SlotName.TRAVELERS: {
            "just me": {"adults": 1},
            "two adults": {"adults": 2},
            "two adults and a child": {"adults": 2, "children": 1},
        },
        SlotName.ORIGIN: {
            "philadelphia": {"origin": {"name": "Philadelphia", "city": "Philadelphia"}},
            "new york": {"origin": {"name": "New York", "city": "New York"}},
        },
        SlotName.BUDGET: {
            "5000 dollars": {"budget": {"amount": 5000, "currency": "USD"}},
        },
        SlotName.FLIGHT_PREFERENCES: {
            "economy": {"cabin": "economy"},
            "economy, nonstop": {"cabin": "economy", "max_stops": 0},
        },
        SlotName.LODGING_PREFERENCES: {
            "a midrange hotel": {"hotel_class": "midrange", "kind": "hotel"},
        },
        SlotName.ACTIVITY_PREFERENCES: {
            "museums and food": {"interests": ["museum", "food"]},
        },
    }


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


def _properties(schema: JsonValue) -> dict[str, JsonValue]:
    """A JSON schema's `properties`, or {} when it has none."""
    properties = schema.get("properties") if isinstance(schema, dict) else None
    return properties if isinstance(properties, dict) else {}


def _slot_for_schema(schema: dict[str, JsonValue]) -> SlotName | None:
    """The slot an extraction schema is for, by the fields of its `value`."""
    properties = _properties(schema)
    if set(properties) != {"status", "value"}:
        return None
    fields = set(_properties(properties["value"]))
    return next(
        (
            spec.name
            for spec in SLOTS.values()
            if spec.value_model is not None and set(spec.value_model.model_fields) == fields
        ),
        None,
    )


def _latest_user_text(request: FakeRequest) -> str:
    return next((m.content for m in reversed(request.messages) if m.role == "user"), "")


def extract_reply(slot: SlotName, content: str, today: date) -> dict[str, JsonValue]:
    phrase = content.strip().rstrip(".!").casefold()
    value = _phrases(today)[slot].get(phrase)
    if value is not None:
        return {"status": "answered", "value": value}
    if content.strip().endswith("?"):
        return {"status": "question", "value": None}
    return {"status": "unrelated", "value": None}


_ORDINALS = ("first", "second", "third", "fourth", "fifth")


def pick_reply(content: str) -> dict[str, JsonValue]:
    phrase = content.strip().rstrip(".!").casefold().removeprefix("the ").removesuffix(" one")
    if phrase in _ORDINALS:
        return {"status": "answered", "candidate_ids": [str(_ORDINALS.index(phrase) + 1)]}
    if content.strip().endswith("?"):
        return {"status": "question", "candidate_ids": []}
    return {"status": "unrelated", "candidate_ids": []}


def _is_pick_schema(schema: dict[str, JsonValue]) -> bool:
    return set(_properties(schema)) == {"status", "candidate_ids"}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

# A line of the numbered itinerary (`prompts.item_line`): number, type and title.
_ITEM_LINE = re.compile(r"^(\d+)\. (\w+) “(.*?)”", re.MULTILINE)
_STAY_WORDS = ("hotel", "stay")
# The stage the command prompt says the plan is at (`prompts.command_messages`).
_PLAN_AT = re.compile(r"The plan is at (\w+)\.")
# What "show me other <words>" asks to search again.
_SEARCH_WORDS: dict[str, SearchName] = {
    "hotels": "lodging",
    "places to stay": "lodging",
    "flights": "flights",
    "things to do": "activities",
    "activities": "activities",
}
_EDIT_PREFIXES = ("remove ", "go back", "show me other ", "make the hotel ")


def _phrase(content: str) -> str:
    return content.strip().rstrip(".!").casefold()


def is_edit(content: str) -> bool:
    """Whether the readers hand `content` on as a command."""
    phrase = _phrase(content)
    return phrase.startswith(_EDIT_PREFIXES) or phrase == "skip flights"


def _item_number(words: str, prompt: str) -> int | None:
    """The first item in the prompt's list that `words` names."""
    for number, kind, title in _ITEM_LINE.findall(prompt):
        # "the hotel" means a stay, not the "Hotel dinner".
        names = kind == "lodging" if words in _STAY_WORDS else words in title.casefold()
        if names:
            return int(number)
    return None


def _stage(name: str) -> PlanStage | None:
    return next((stage for stage in PlanStage if name in (stage.value, STAGE_LABELS[stage])), None)


def _search(words: str, prompt: str) -> SearchName | None:
    if words != "options":
        return _SEARCH_WORDS.get(words)
    at = _PLAN_AT.search(prompt)
    stage = at.group(1) if at else ""
    return next((name for name in get_args(SearchName) if name == stage), None)


def _command(phrase: str, prompt: str) -> dict[str, JsonValue] | None:
    if phrase == "skip flights":
        return {"type": "change_stage", "action": "skip_flights"}
    if phrase == "go back":
        return {"type": "change_stage", "action": "back"}
    if phrase.startswith("go back to "):
        stage = _stage(phrase.removeprefix("go back to ").removeprefix("the "))
        if stage is None:
            return None
        return {"type": "change_stage", "action": "go_to", "stage": stage.value}
    if phrase.startswith("remove "):
        number = _item_number(phrase.removeprefix("remove ").removeprefix("the "), prompt)
        return None if number is None else {"type": "remove_item", "item": number}
    if phrase.startswith("show me other "):
        search = _search(phrase.removeprefix("show me other "), prompt)
        return None if search is None else {"type": "redo_search", "search": search}
    if phrase.startswith("make the hotel "):
        hotel_class = phrase.removeprefix("make the hotel ")
        if hotel_class not in get_args(HotelClass):
            return None
        return {"type": "set_lodging_preferences", "hotel_class": hotel_class}
    return None


def command_reply(content: str, prompt: str) -> dict[str, JsonValue]:
    """The command reader's reply to `content`, given the system `prompt` it was sent."""
    command = _command(_phrase(content), prompt)
    if command is not None:
        return {"status": "command", "commands": [command]}
    if content.strip().endswith("?"):
        return {"status": "question", "commands": []}
    return {"status": "unrelated", "commands": []}


def _is_command_schema(schema: dict[str, JsonValue]) -> bool:
    return set(_properties(schema)) == {"status", "commands", "clarification"}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def planning_reply(request: FakeRequest) -> LLMMessage:
    """The fake's reply to any request the app makes (`FakeLLM`'s offline fallback)."""
    if request.tools:
        # Only the planner's loop sends tools; the stage flow's readers send a format.
        return agent_reply(request)
    content = _latest_user_text(request)
    if request.format is not None and _is_command_schema(request.format):
        prompt = request.messages[0].content if request.messages else ""
        return json_reply(command_reply(content, prompt))
    if request.format is not None and is_edit(content):
        # Both readers have a "command" status; neither gives a value with it.
        return json_reply({"status": "command"})
    if request.format is not None and _is_pick_schema(request.format):
        return json_reply(pick_reply(content))
    if request.format is not None:
        slot = _slot_for_schema(request.format)
        if slot is None:
            return json_reply({"status": "unrelated", "value": None})
        return json_reply(extract_reply(slot, content, date.today()))
    return text(OFFLINE_ANSWER)
