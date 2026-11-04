"""Canned model replies for the planning flow under `LLM_PROVIDER=fake` (offline dev and the
Playwright suite), so free text works with no model.

- Extraction requests are recognised by their `format` schema: the fields of its `value`
  name the slot. The latest user message is looked up, case-folded, in a small phrase
  table for that slot; a match is answered, text ending in "?" is a question, and anything
  else is unrelated.
- Question requests (`prompts.QA_INTRO`) get a fixed answer.

Anything else returns None, so the chat flow's own fake (`chat.offline`) handles it.
"""

from datetime import date, timedelta
from typing import Any

from features.planning.prompts import QA_INTRO
from features.planning.slots import SLOTS
from features.plans.schemas import SlotName
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


def _dates(start: date, nights: int) -> dict[str, Any]:
    end = start + timedelta(days=nights)
    return {"dates": {"start": start.isoformat(), "end": end.isoformat()}}


def _phrases(today: date) -> dict[SlotName, dict[str, dict[str, Any]]]:
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


def _slot_for_schema(schema: dict[str, Any]) -> SlotName | None:
    """The slot an extraction schema is for, by the fields of its `value`."""
    properties = schema.get("properties", {})
    if set(properties) != {"status", "value"}:
        return None
    fields = set(properties["value"].get("properties", {}))
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


def extract_reply(slot: SlotName, content: str, today: date) -> dict[str, Any]:
    phrase = content.strip().rstrip(".!").casefold()
    value = _phrases(today)[slot].get(phrase)
    if value is not None:
        return {"status": "answered", "value": value}
    if content.strip().endswith("?"):
        return {"status": "question", "value": None}
    return {"status": "unrelated", "value": None}


def planning_reply(request: FakeRequest) -> LLMMessage | None:
    """The fake's reply to a planning request, or None when the request isn't one."""
    if request.format is not None:
        slot = _slot_for_schema(request.format)
        if slot is None:
            return None
        return json_reply(extract_reply(slot, _latest_user_text(request), date.today()))
    system = request.messages[0].content if request.messages else ""
    if request.streamed and system.startswith(QA_INTRO):
        return text(OFFLINE_ANSWER)
    return None
