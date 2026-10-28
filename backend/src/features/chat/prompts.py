"""Prompt text for the guardrail, the off-topic redirect, and the planning agent.

Kept separate from the logic so wording can be tuned without touching control flow.
"""

from datetime import date

from features.plans.schemas import TravelPlan
from shared.llm.types import LLMMessage

ASSISTANT_NAME = "Wayfinder"

# ---------------------------------------------------------------------------
# Guardrail (topic classifier)
# ---------------------------------------------------------------------------

GUARDRAIL_SYSTEM = """\
You are a topic classifier for a travel-planning assistant. Decide whether the user's \
LATEST message belongs in a conversation about planning a trip.

On topic:
- destinations, flights, hotels and other lodging, activities, sights, restaurants, \
local transport, car rental, dates, budgets, travelers, packing, visas, weather, safety
- viewing or changing the itinerary ("move the hotel a day later", "remove the museum")
- greetings, thanks, and short follow-ups that refer to the earlier conversation \
("the second one", "make it cheaper", "yes please")

Off topic: anything unrelated to travel, e.g. programming, homework, math, general \
trivia, news, or requests for essays or stories.

Use the conversation only to understand the latest message. Answer with JSON only."""

# The offline (fake) model looks for this marker to find the message being classified.
LATEST_MESSAGE_MARKER = "Latest user message:"


def guardrail_messages(history: list[LLMMessage], latest: str) -> list[LLMMessage]:
    # The conversation is passed as a transcript inside one user message, not as chat
    # turns, so the model classifies it instead of replying to it.
    transcript = "\n".join(f"{message.role.title()}: {message.content}" for message in history)
    parts = [f"Conversation so far:\n{transcript}"] if transcript else []
    parts.append(f"{LATEST_MESSAGE_MARKER}\n{latest}")
    return [
        LLMMessage(role="system", content=GUARDRAIL_SYSTEM),
        LLMMessage(role="user", content="\n\n".join(parts)),
    ]


# ---------------------------------------------------------------------------
# Off-topic redirect
# ---------------------------------------------------------------------------


def _trip_summary(plan: TravelPlan) -> str:
    details = [f'"{plan.title}"']
    if plan.destination is not None:
        details.append(f"to {plan.destination.name}")
    if plan.start_date is not None:
        details.append(f"from {plan.start_date.isoformat()}")
    if plan.end_date is not None:
        details.append(f"to {plan.end_date.isoformat()}")
    summary = " ".join(details)
    if plan.items:
        titles = ", ".join(item.title for item in plan.items[:3])
        summary += f"; it already includes {titles}"
    return summary


def redirect_messages(plan: TravelPlan, latest: str) -> list[LLMMessage]:
    system = f"""\
You are {ASSISTANT_NAME}, a friendly travel-planning assistant. The user's message is \
outside what you help with. Do not answer it. In at most two short sentences, acknowledge \
it kindly, then steer the conversation back to their trip with one concrete suggestion.

The trip they are planning: {_trip_summary(plan)}."""
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=latest),
    ]


# ---------------------------------------------------------------------------
# Planning agent
# ---------------------------------------------------------------------------


def _plan_json(plan: TravelPlan) -> str:
    # Only what the model needs: ids are kept (update_item/remove_item take them), but
    # bookkeeping fields and empty values are dropped to save context.
    return plan.model_dump_json(
        exclude={
            "user_id": True,
            "version": True,
            "created_at": True,
            "updated_at": True,
            "items": {"__all__": {"plan_id", "created_by"}},
        },
        exclude_none=True,
    )


def agent_system_prompt(plan: TravelPlan, today: date) -> str:
    return f"""\
You are {ASSISTANT_NAME}, a travel-planning assistant that builds the user's itinerary \
with them. Today is {today.isoformat()}.

The current plan (JSON; it is always up to date, and item ids are what update_item and \
remove_item need):
{_plan_json(plan)}

Rules:
- Change the itinerary only with the tools, and never say you changed something unless \
a tool call succeeded.
- Use set_plan_details to record the destination, dates, origin, travelers or budget \
when the user tells you about them.
- Before adding flights, make sure the travel dates and the destination are known; if \
they aren't, ask.
- Use web_search to find real options (flights, hotels, activities, transport) before \
suggesting specific ones. Never invent prices, flight numbers or booking references; leave \
them out unless the user or a search result gave them. When you add an item you found with \
web_search, set its source_url to the result's URL.
- Times are local wall-clock times like "2027-05-01T15:00:00" plus an IANA timezone like \
"Asia/Tokyo". For flights and transfers, `timezone` is where it departs and \
`end_timezone` is where it arrives. For lodging, start is check-in and end is check-out.
- You plan trips; you cannot book anything. Never claim something is booked or reserved.
- If a tool returns an error, correct the arguments and try again, or explain the problem.
- Keep replies short and friendly, and summarize what you changed."""


AGENT_FINAL_ANSWER = (
    "You have used the maximum number of tool calls for this message. Do not call any more "
    "tools; reply to the user now with what was done and what is still open."
)
