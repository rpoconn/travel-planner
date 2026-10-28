"""Canned behavior for `LLM_PROVIDER=fake`: lets the app run without Ollama (offline dev,
and later the Playwright E2E suite). A keyword check stands in for the guardrail."""

import re

from features.chat.prompts import LATEST_MESSAGE_MARKER
from features.chat.schemas import GuardrailVerdict
from shared.llm.fake import FakeRequest, json_reply, text
from shared.llm.types import LLMMessage

_TRAVEL_WORDS = frozenset(
    {
        "trip", "travel", "flight", "flights", "fly", "hotel", "hotels", "stay", "airport",
        "itinerary", "plan", "vacation", "holiday", "visit", "tour", "museum", "beach",
        "train", "car", "rental", "budget", "day", "days", "night", "nights", "restaurant",
        "hi", "hello", "hey", "thanks", "yes", "no",
    }
)  # fmt: skip

OFFLINE_AGENT_REPLY = (
    "I'm running in offline mode (LLM_PROVIDER=fake), so I can't plan anything yet. "
    "Start Ollama and set LLM_PROVIDER=ollama to chat for real."
)
OFFLINE_REDIRECT_REPLY = "That's outside what I can help with, so let's get back to your trip!"


def _latest_user_text(request: FakeRequest) -> str:
    content = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    return content.rsplit(LATEST_MESSAGE_MARKER, 1)[-1]


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
    return text(OFFLINE_AGENT_REPLY if request.tools else OFFLINE_REDIRECT_REPLY)
