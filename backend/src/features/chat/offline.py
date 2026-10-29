"""Canned behavior for `LLM_PROVIDER=fake`: lets the app run without Ollama (offline dev and
the Playwright E2E suite). A keyword check stands in for the guardrail, and one scripted
command ("add <something>") makes the agent call `add_activity`, so a full chat → tool →
timeline round trip works with no model.
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from features.chat.prompts import LATEST_MESSAGE_MARKER
from features.chat.schemas import GuardrailVerdict
from shared.llm.fake import FakeRequest, json_reply, text, tool_calls
from shared.llm.types import LLMMessage

# ---------------------------------------------------------------------------
# Canned text
# ---------------------------------------------------------------------------

_TRAVEL_WORDS = frozenset(
    {
        "trip", "travel", "flight", "flights", "fly", "hotel", "hotels", "stay", "airport",
        "itinerary", "plan", "vacation", "holiday", "visit", "tour", "museum", "beach",
        "train", "car", "rental", "budget", "day", "days", "night", "nights", "restaurant",
        "add", "hi", "hello", "hey", "thanks", "yes", "no",
    }
)  # fmt: skip

OFFLINE_AGENT_REPLY = (
    "I'm running in offline mode (LLM_PROVIDER=fake), so I can't plan anything yet. "
    "Start Ollama and set LLM_PROVIDER=ollama to chat for real. To try an itinerary "
    'edit anyway, send "add" and an activity, e.g. "Add a visit to the Louvre".'
)
OFFLINE_REDIRECT_REPLY = "That's outside what I can help with, so let's get back to your trip!"
OFFLINE_ADDED_REPLY = "Done! It's on your itinerary. Click the card on the timeline for details."

# "Add a visit to the Louvre" -> "visit to the Louvre"
_ADD_COMMAND = re.compile(r"^\s*add\s+(?:an?\s+|the\s+)?(?P<what>\S.*?)[\s.!]*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _latest_user_text(request: FakeRequest) -> str:
    content = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    return content.rsplit(LATEST_MESSAGE_MARKER, 1)[-1]


def offline_activity(what: str, today: date) -> dict[str, Any]:
    """`add_activity` arguments for "add <what>": two hours tomorrow morning, in UTC."""
    title = (what[:1].upper() + what[1:])[:200]
    day = today + timedelta(days=1)
    return {
        "title": title,
        "place": {"name": title},
        "category": "sightseeing",
        "start": datetime.combine(day, time(10)).isoformat(),
        "end": datetime.combine(day, time(12)).isoformat(),
        "timezone": "UTC",
    }


# ---------------------------------------------------------------------------
# Fallback reply
# ---------------------------------------------------------------------------


def offline_reply(request: FakeRequest) -> LLMMessage:
    if request.format is not None:  # the guardrail is the only structured-output call
        words = set(re.findall(r"[a-z]+", _latest_user_text(request).lower()))
        on_topic = bool(words & _TRAVEL_WORDS)
        return json_reply(
            GuardrailVerdict(
                on_topic=on_topic, confidence=0.9, topic="travel" if on_topic else "other"
            )
        )
    # The agent always offers tools; the redirect never does.
    if not request.tools:
        return text(OFFLINE_REDIRECT_REPLY)
    # Second agent round: the tool already ran, so wrap up. (Failed tools report "Error: …".)
    if request.messages and request.messages[-1].role == "tool":
        result = request.messages[-1].content
        if result.startswith("Error"):
            return text(f"I couldn't add that. {result}")
        return text(OFFLINE_ADDED_REPLY)
    command = _ADD_COMMAND.match(_latest_user_text(request))
    if command is not None:
        arguments = offline_activity(command.group("what"), date.today())
        return tool_calls(("add_activity", arguments))
    return text(OFFLINE_AGENT_REPLY)
