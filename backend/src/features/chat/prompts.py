"""Prompt text for the guardrail, the off-topic redirect, the planning agent, and the
messages that tell the agent how the user answered a proposal card.

Kept separate from the logic so wording can be tuned without touching control flow.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from uuid import UUID

from features.chat.schemas import ProposedChange
from features.itinerary.schemas import TimelineWarning
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


# Longer trips skip the calendar: the list would cost more context than it saves.
_MAX_CALENDAR_DAYS = 31


def _trip_calendar(plan: TravelPlan) -> str:
    # Small models can't reliably work out the weekday of a date (one called 2027-05-04,
    # a Tuesday, "Thursday"), so every trip day is spelled out for them to read off.
    if plan.start_date is None or plan.end_date is None:
        return ""
    length = (plan.end_date - plan.start_date).days + 1
    if not 0 < length <= _MAX_CALENDAR_DAYS:
        return ""
    days = (plan.start_date + timedelta(days=offset) for offset in range(length))
    listed = ", ".join(f"{day:%a} {day.isoformat()}" for day in days)
    return (
        "\n\nTrip calendar (whenever you name a weekday, read it from here; never work one "
        f"out yourself):\n{listed}"
    )


def _next_may(today: date) -> int:
    return today.year if today.month <= 5 else today.year + 1


def _future_month_example(today: date) -> str:
    # Last month is always the case a model gets wrong ("in September" said in October
    # means next year), so the example uses it. January's last month is this December.
    if today.month == 1:
        month = date(today.year, 12, 1)
    else:
        month = date(today.year + 1, today.month - 1, 1)
    return f'"in {month:%B}" means {month:%B %Y}'


def agent_system_prompt(plan: TravelPlan, today: date) -> str:
    # Written for small local models: short rules, the riskiest ones first, and one worked
    # example of asking instead of guessing.
    return f"""\
You are {ASSISTANT_NAME}, a travel-planning assistant that builds the user's itinerary \
with them. Today is {today:%A} {today.isoformat()} ({today:%B} {today.day}, {today.year}).

The current plan (JSON; it is always up to date, and item ids are what update_item and \
remove_item need; a field that is missing is unknown):
{_plan_json(plan)}{_trip_calendar(plan)}

How changes work:
- Calling set_plan_details, an add_* tool, update_item or remove_item does not change the \
trip. It adds the change to a proposal card the user sees, with Approve / Deny / Other \
buttons. Nothing is applied until they approve.
- So never say you added, set or changed something; say you proposed it. Don't ask \
"shall I apply it?" either: the card does that.
- Group the related changes of one step into one proposal, then stop calling tools and \
reply in 1-2 sentences: what you proposed, plus any question you still have.
- When a tool answers "Staged", that change is on the card: do not call it again. \
Proposed items have no id until they are approved.
- After the user approves, you are told what was applied. Confirm it in one sentence and \
suggest the next step.

Before set_plan_details or any add_* tool, make sure the user has told you:
- travel dates, number of travelers (adults and children), and where they start from;
- for flights: airline, departure-time and cabin preferences;
- for lodging: area, type and budget;
- for meals: dietary needs, and whether they want a reservation.
If the plan JSON has no travelers or no dates, ask for them. Never assume one adult and \
never fill in plausible defaults for anything the user has not said. Ask for what is \
missing (at most three questions at a time) and propose only what they told you.

Example. User: "I want to spend a week in Tokyo in May". Don't call a tool yet. Reply: \
"Tokyo in May sounds great! Which dates in May {_next_may(today)} work for you, how many \
of you are going (adults and children), and where will you fly from?"

Other rules:
- Trips are in the future. A month or date without a year is its next occurrence on or \
after today: {_future_month_example(today)}. Never propose a date before today.
- Use web_search to find real options (flights, hotels, activities, transport) before \
suggesting specific ones. Never invent prices, flight numbers or booking references; leave \
them out unless the user or a search result gave them. When you propose an item you found \
with web_search, set its source_url to the result's URL.
- Times are local wall-clock times like "2027-05-01T15:00:00" plus an IANA timezone like \
"Asia/Tokyo". For flights and transfers, `timezone` is where it departs and \
`end_timezone` is where it arrives. For lodging, start is check-in and end is check-out.
- A flight's end is the local arrival time at the destination airport. A long-haul flight \
takes many hours, so its end often falls on the next calendar day; it is never the same \
instant as departure. Use a real airport code (HND or NRT, not the city code TYO). The \
flight home is direction "return", not a second "outbound".
- You plan trips; you cannot book anything. Never claim something is booked or reserved.
- If a tool returns an error, correct the arguments and try again, or explain the problem.
- Keep replies short and friendly."""


AGENT_FINAL_ANSWER = (
    "You have used the maximum number of tool calls for this message. Do not call any more "
    "tools; reply to the user now with what you proposed and what is still open."
)

# Tool result for a change that is already on this turn's proposal card.
ALREADY_STAGED = (
    "Already staged; do not repeat it. Stop calling tools for this change and reply to the "
    "user with what you proposed."
)


# ---------------------------------------------------------------------------
# "Fix it" request (a timeline warning handed to the agent)
# ---------------------------------------------------------------------------

# The offline (fake) model looks for this marker to recognise a fix request.
FIX_REQUEST_MARKER = "Itinerary warning to fix:"
# The topic reported for a fix turn, which skips the topic classifier.
FIX_GUARDRAIL_TOPIC = "itinerary fix"

_FIX_INSTRUCTIONS = """\
Help the user fix this warning:
- in one sentence, say why this is a problem;
- if the fix is clear, propose it with the tools (update_item, remove_item or an add_* \
tool); the user approves it on the card;
- if there are 2-3 reasonable fixes, describe them briefly and ask which one they want, \
without calling a tool;
- ask instead of guessing whenever a detail you need is not in the conversation (which \
hotel, what time, which item to move)."""


def _describe_item(item_id: UUID, plan: TravelPlan) -> str:
    # The client's warning can be stale (the plan changed since it was computed), so an id
    # that's no longer in the plan is reported rather than treated as an error.
    item = next((item for item in plan.items if item.id == item_id), None)
    if item is None:
        return f"- {item_id}: no longer in the plan"
    return f"- {item_id}: {item.title} ({item.type}, {item.start:%Y-%m-%d %H:%M})"


def fix_request_message(content: str, fix: TimelineWarning, plan: TravelPlan) -> str:
    """The agent's latest user message for a "Fix it" turn: the visible text plus the
    warning details and instructions. Only `content` is stored; the details reach later
    turns through the proposal the agent makes."""
    lines = [content, "", FIX_REQUEST_MARKER, f"kind: {fix.kind}", f"message: {fix.message}"]
    if fix.date is not None:
        lines.append(f"date: {fix.date.isoformat()}")
    if fix.item_ids:
        lines.append("items:")
        lines.extend(_describe_item(item_id, plan) for item_id in fix.item_ids)
    lines.extend(["", _FIX_INSTRUCTIONS])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Proposal decisions (the user answered a proposal card)
# ---------------------------------------------------------------------------

# The offline (fake) model looks for these markers to recognise a decision turn.
APPROVAL_MARKER = "The user approved your proposed changes."
REVISE_MARKER = "The user did not approve your proposed changes. Instead they said:"


def _bullets(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def approval_applied_message(applied: Sequence[str]) -> str:
    """The agent's latest message after an approval: what was applied (tool results)."""
    return (
        f"{APPROVAL_MARKER} These changes are now applied:\n{_bullets(applied)}\n\n"
        "Confirm this in one short sentence and suggest the next step. Propose more "
        "changes only if the user already gave you the details."
    )


def approval_failed_message(applied: Sequence[str], failures: Sequence[str]) -> str:
    """The agent's latest message when an approved proposal could not be applied."""
    parts = [
        f"{APPROVAL_MARKER} But it could no longer be applied, because the plan changed "
        f"since you proposed it:\n{_bullets(failures)}"
    ]
    if applied:
        parts.append(f"These changes were applied before the failure:\n{_bullets(applied)}")
    else:
        parts.append("Nothing was applied.")
    parts.append("Tell the user briefly what went wrong and propose a corrected change if you can.")
    return "\n\n".join(parts)


def revise_request_message(text: str, changes: Sequence[ProposedChange]) -> str:
    """The agent's latest message for "Other…": the user's text plus what it replaces.
    Only `text` is stored."""
    return (
        f"{REVISE_MARKER}\n{text}\n\nYour proposal, which was not applied:\n"
        f"{_bullets([change.summary for change in changes])}\n\n"
        "Answer them. If they asked for different changes, propose the corrected set (the "
        "whole step, not just the difference)."
    )
