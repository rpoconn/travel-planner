"""Prompt text for the guardrail, the off-topic redirect, the planning agent, and the
messages that tell the agent how the user answered a proposal card.

Kept separate from the logic so wording can be tuned without touching control flow.
"""

import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from features.chat.schemas import ProposedChange
from features.itinerary.gaps import describe_place
from features.itinerary.schemas import Gap, TimelineWarning
from features.plans.schemas import STAGES, PlanSnapshot, PlanStage, StageStatus
from features.plans.stages import STAGE_LABELS, stage_status
from shared.costs import budget_summary, totals_by_currency
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


def _trip_summary(plan: PlanSnapshot) -> str:
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


def redirect_messages(plan: PlanSnapshot, latest: str) -> list[LLMMessage]:
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


def _plan_json(plan: PlanSnapshot) -> str:
    # Only what the model needs: ids are kept (update_item/remove_item take them), but
    # bookkeeping fields and empty values are dropped to save context.
    return plan.model_dump_json(
        exclude={
            "user_id": True,
            "version": True,
            "created_at": True,
            "updated_at": True,
            "items": {"__all__": {"plan_id", "created_by"}},
            # The workflow is described in words by `_stage_block` instead.
            "stage": True,
            "flights_skipped": True,
            "stages": True,
            "gaps": True,
        },
        exclude_none=True,
    )


# ---------------------------------------------------------------------------
# Stage block (where the plan is in the workflow)
# ---------------------------------------------------------------------------

_STAGE_PURPOSES: dict[PlanStage, str] = {
    PlanStage.BASICS: (
        "agree the trip's dates, who is travelling, where it starts and the destination, "
        "and save them with set_plan_details"
    ),
    PlanStage.FLIGHTS: "propose the flight out and the flight home",
    PlanStage.LODGING: "find somewhere to stay for every night of the trip",
    PlanStage.ACTIVITIES: "plan things to do and places to eat",
    PlanStage.GROUND: (
        "add a transfer for every change of place, from leaving home to getting back home"
    ),
    PlanStage.REVIEW: (
        "the trip is complete; answer questions and fix anything the user wants changed"
    ),
}

# Which stage a request belongs to, so the agent can redirect instead of forcing it into
# this stage's tool (a small model once booked a dinner with add_lodging, kind "other").
_STAGE_OWNERSHIP = (
    "What belongs where: flights to the flights stage; hotels and other places to sleep to "
    "lodging; sights, tours, meals and restaurants to activities; taxis, trains, car "
    "rentals and other rides between places to ground transport."
)

# The offline (fake) model finds the gaps after this marker, one `GAP_LINE` each.
GAPS_MARKER = "Transfers still needed:"
_OPEN = "any time"
# One gap as `format_gap` writes it. Times are local wall-clock in the gap's timezone.
GAP_LINE = re.compile(
    r'^- from "(?P<from_name>[^"]*)" to "(?P<to_name>[^"]*)" \| leave after: '
    r"(?P<leave>[^|]+?) \| arrive by: (?P<arrive>[^|]+?) \| timezone: (?P<timezone>\S+)$",
    re.MULTILINE,
)


def _local(moment: datetime | None, timezone: str) -> str:
    if moment is None:
        return _OPEN
    return moment.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%dT%H:%M")


def format_gap(gap: Gap) -> str:
    """One line per gap for the ground stage (parsed back by `GAP_LINE`)."""
    return (
        f'- from "{describe_place(gap.from_place, gap.after_item_id)}" '
        f'to "{describe_place(gap.to_place, gap.before_item_id)}" '
        f"| leave after: {_local(gap.leave_after, gap.timezone)} "
        f"| arrive by: {_local(gap.arrive_before, gap.timezone)} "
        f"| timezone: {gap.timezone}"
    )


def parse_gap_line(line: str) -> dict[str, str | None] | None:
    """A `format_gap` line's parts (None for an open time), or None if it isn't one."""
    match = GAP_LINE.match(line.strip())
    if match is None:
        return None
    parts: dict[str, str | None] = dict(match.groupdict())
    for key in ("leave", "arrive"):
        if parts[key] == _OPEN:
            parts[key] = None
    return parts


# Asked before any hotel search: without this, "Add a hotel in Paris" (the stage's button)
# sent models straight to web_search and the first hotel found, with no idea of the user's
# class, budget or area.
_LODGING_INSTRUCTIONS = (
    "Before you search for or propose any lodging, you need the user's preferences: the "
    "hotel class they want (star rating, or a type such as hotel, apartment or hostel), "
    "their budget for lodging (per night or for the whole stay), and the neighborhood or "
    "area they want to stay in. If the conversation does not already give all three, ask "
    "for the missing ones in one reply, as plain questions (no web_search or add_lodging "
    'yet), even when the user just said "add a hotel" or "find a hotel"; suggest two or '
    "three well-known areas to choose from when you know the destination. Only when you "
    "have them, search with web_search for places matching all three and propose one with "
    "add_lodging. Its source_url must be the URL of the web_search result that listed the "
    "place (a result from this or an earlier reply), not the hotel's own website unless a "
    "result had that address; if unsure, leave source_url out rather than guess."
)

# qwen3:8b, asked to add three activities, searched the web and then wrote "I've added
# the following activities to your itinerary" without calling add_activity once. Unlike
# flights and lodging, it had nothing telling it how to schedule an activity.
_ACTIVITIES_INSTRUCTIONS = (
    "Suggest activities with web_search first and let the user pick. Once they say which "
    'ones they want ("add those", "sounds good"), call add_activity once per activity '
    "in the same reply, all on one card; describing them in text adds nothing. Each call "
    "needs: the place (name and address), a category (sightseeing, food, tour, museum, "
    "outdoors, nightlife, shopping, relax or other), and a start and end on a trip day "
    "after the traveller has arrived and before they leave, in the destination's "
    "timezone. Spread the activities over different days, give each a sensible local "
    "time and length (about two hours for a museum or sight, 19:00-21:00 for dinner), "
    "and set source_url to the web result you found it in."
)

_GROUND_INSTRUCTIONS = (
    "For each transfer still needed, ask the user how they want to make it (taxi, train, "
    "rental car, shuttle...), at most three questions at a time; suggest a sensible mode "
    "when there is an obvious one. Once you know the modes, propose every transfer on one "
    "card with add_transfer, one call per line above, leaving after and arriving by its "
    "times. Use the plan's origin as the place for home."
)


def _checklist(status: StageStatus) -> str:
    if status.ready:
        return "Checklist: complete."
    return f"Still missing: {'; '.join(status.missing)}."


def _stage_block(plan: PlanSnapshot) -> str:
    stage = plan.stage
    status = stage_status(plan, stage)
    index = STAGES.index(stage)
    lines = [
        f"Current stage: {STAGE_LABELS[stage]} ({index + 1} of {len(STAGES)}): "
        f"{_STAGE_PURPOSES[stage]}.",
        _checklist(status),
        "You only have this stage's tools. The user moves between stages with the Next and "
        "Back buttons; you cannot. If they ask for something from another stage, never use "
        "this stage's tools for it (a restaurant is not lodging): tell them which stage it "
        "belongs to and that they can go there with Next or Back.",
        _STAGE_OWNERSHIP,
    ]
    if stage != "review":
        upcoming = STAGE_LABELS[STAGES[index + 1]]
        lines.append(f"When the checklist is complete, suggest pressing Next (next: {upcoming}).")
    else:
        lines.append(
            "This is the last stage and there is no Next button: when the checklist is "
            "complete, tell the user their trip is complete."
        )
    if stage == "basics":
        if plan.origin is not None:
            lines.append(
                "The origin is prefilled from the user's home: confirm it with them rather "
                "than asking where they start. Never replace it with just an airport code; "
                "to set the nearest airport, add its iata code to the origin instead."
            )
        else:
            lines.append("Ask where the trip starts (their home city or address).")
    if plan.flights_skipped and index > STAGES.index("flights"):
        lines.append(
            "The user skipped flights (they are driving or taking ground transport), so "
            "the ground stage covers the whole journey from home."
        )
    if stage == "lodging":
        lines.append(_LODGING_INSTRUCTIONS)
    if stage == "activities":
        lines.append(_ACTIVITIES_INSTRUCTIONS)
    if stage == "ground" and plan.gaps:
        lines.append(GAPS_MARKER)
        lines.extend(format_gap(gap) for gap in plan.gaps)
        lines.append(_GROUND_INSTRUCTIONS)
    return "\n\nPlanning stage:\n" + "\n".join(lines)


# Longer trips skip the calendar: the list would cost more context than it saves.
_MAX_CALENDAR_DAYS = 31


def _trip_calendar(plan: PlanSnapshot) -> str:
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


def _cost_summary(plan: PlanSnapshot) -> str:
    # Cancelled items stay in the plan but cost nothing.
    totals = totals_by_currency(item.cost for item in plan.items if item.status != "cancelled")
    summary = budget_summary(plan.budget, totals)
    return f"\n\n{summary}" if summary else ""


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


def agent_system_prompt(plan: PlanSnapshot, today: date) -> str:
    # Written for small local models: short rules, the riskiest ones first, and one worked
    # example of asking instead of guessing.
    return f"""\
You are {ASSISTANT_NAME}, a travel-planning assistant that builds the user's itinerary \
with them. Today is {today:%A} {today.isoformat()} ({today:%B} {today.day}, {today.year}).

The current plan (JSON; it is always up to date, and item ids are what update_item and \
remove_item need; a field that is missing is unknown):
{_plan_json(plan)}{_stage_block(plan)}{_trip_calendar(plan)}{_cost_summary(plan)}

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
- Describing a change in your reply proposes nothing: only a tool call puts it on a card. \
Earlier replies may end with a "[Proposed changes (…): …]" note; the app adds those to \
record past cards. Never write one yourself.

The planning stage above decides what to work on now; within it, before \
set_plan_details or any add_* tool, make sure the user has told you:
- travel dates, number of travelers (adults and children), and where they start from;
- for flights: airline, departure-time and cabin preferences (they apply to the flight \
home too unless the user says otherwise);
- for lodging: hotel class (star rating or type), budget and neighborhood or area;
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
- An item's cost is the total for everyone travelling, not per person: multiply a \
per-person price by the number of travelers.
- If the plan has a budget, every item needs a cost in the budget's currency: basis \
"exact" for a price the user or a web_search result gave, otherwise your best estimate \
with basis "estimate" (0 for something free).
- When the user caps what one kind of activity may cost per person ("dinners under $200 \
a person"), save it as a limit with set_plan_details. The plan's `limits` are those caps.
- The tools reject an item over a limit or over the budget. Then pick a cheaper option, \
or tell the user and ask whether to raise the limit or budget. Never lower a price just \
to make it fit.
- Only call a price "within", "under" or "in" a budget or limit when its number really \
is below it.
- Times are local wall-clock times like "2027-05-01T15:00:00" plus an IANA timezone like \
"Asia/Tokyo". For flights and transfers, `timezone` is where it departs and \
`end_timezone` is where it arrives. For lodging, start is check-in and end is check-out.
- Once the traveller has flown to the destination, everything there (lodging, activities, \
meals, cars, trains) uses the destination's timezone, not their home one.
- A flight's end is the local arrival time at the destination airport. A long-haul flight \
takes many hours, so its end often falls on the next calendar day; it is never the same \
instant as departure. Use a real airport code (HND or NRT, not the city code TYO).
- Trips are round trips. Propose a trip's flights with choose_flights: it searches real \
flights, picks the shortest then cheapest, and proposes the flight out and the flight home \
with their exact times and price. Don't pick flights or work out flight times yourself. \
Use add_round_trip only for specific flights the user gave you, and add_flight only for \
the legs of a multi-city trip or a one-way trip the user asked for.
- When the user picks something you listed in an earlier reply ("add #1", "the second \
one", "the Granbell"), propose exactly that option, by its name. If you need its address \
or price, search for that place by name and use the result about it. Never swap in a \
different place a new search turned up: if you can't find the one they picked, say so.
- You plan trips; you cannot book anything. Never claim something is booked or reserved.
- If a tool returns an error, correct the arguments and try again, or explain the problem.
- Keep replies short and friendly."""


AGENT_FINAL_ANSWER = (
    "You have used the maximum number of tool calls for this message. Do not call any more "
    "tools; reply to the user now with what you proposed and what is still open."
)

# Told to the model (once a turn) when its proposal has a flight out but none home.
RETURN_FLIGHT_NUDGE = (
    "Your proposal has a flight out but no flight home. Unless the user asked for a one-way "
    'or multi-city trip, propose the return flight now with add_flight (direction "return", '
    "from {away} to {home}{when}) on the same card, with the same airline and cabin."
)

# Told to the model (once a turn) when its reply says it proposed changes but it called
# no tool, so there's no card: small models copy the history's proposal notes instead.
PHANTOM_PROPOSAL_NUDGE = (
    "Your reply says you proposed changes, but you called no tool, so the user has no "
    "proposal card and nothing to approve. If you meant to propose changes, call the tools "
    "for them now, then add at most one short sentence without repeating yourself. "
    "Otherwise add nothing."
)

# Told to the model (once a turn) when its reply claimed a change was made or staged in
# the same round as the tool calls for it, and none of them staged anything: the user has
# read a claim that is false. qwen3:8b wrote "has been added to your itinerary" beside an
# add_lodging call whose source_url was rejected, then "I've staged the following".
FAILED_CLAIM_NUDGE = (
    "Your reply told the user the change was made, but the tool call for it failed (see "
    "the error above), so nothing is proposed and the user has no card to approve. Fix "
    "the call now using the error's guidance and call the tool again; if you cannot, tell "
    "the user plainly that it was not added and why. Never say a change was added, staged "
    "or proposed unless a tool call for it succeeded."
)

# Shown to the user (after the model's text) when the nudge above got the same claim
# again with still no tool call: the streamed claim is false, and this says so.
PHANTOM_PROPOSAL_NOTE = (
    "Nothing has been proposed yet: I described these changes but did not put them on a "
    "card for you to approve. Ask me again and I'll propose them properly."
)

# Told to the model (once a turn) when its reply promises a search but it called no tool,
# so the turn would end before searching. qwen3:8b did this after a source_url it made up
# was rejected: "Let me search for … first. One moment please.", then nothing.
PROMISED_SEARCH_NUDGE = (
    "Your reply says you will search, but you called no tool, so the turn would end "
    "without a search and the user would have to ask again. Call web_search now, then "
    "propose the change if there is one (with a result's URL as source_url, or without "
    "source_url). Add at most one short sentence without repeating yourself."
)

# Tool result for a change that is already on this turn's proposal card.
ALREADY_STAGED = (
    "Already staged; do not repeat it. Stop calling tools for this change and reply to the "
    "user with what you proposed."
)


# ---------------------------------------------------------------------------
# Proposal review (a second, narrow check of what the agent staged)
# ---------------------------------------------------------------------------

# Tuned on qwen3:8b with tests/features/chat/test_review_eval.py. "Judge a change by its
# tool, not its title" is what catches a dinner saved as lodging; a numbered checklist
# (kind, item, date, direction) scored worse, the model ticked every box.
REVIEW_SYSTEM = """\
You check a travel assistant's proposed changes before the user sees them. For each \
change, compare what the user's latest message asked for with what the change really does.

Judge a change by its tool, not its title: add_lodging always saves a place to sleep, even \
when its title says dinner or tour. A change fits when it is the same kind of thing the \
user asked for, with the name, place and dates they gave, or a needed part of it (the \
flight home of a round trip, every transfer when they ask for the transfers). It does not \
fit when it is a different kind of thing (a hotel or a lodging when they asked for dinner \
or a tour, a ride when they asked for a museum), a different place or item than the one \
they named, a different date than they gave, or something extra they did not ask for.

When the user is answering the assistant's previous reply, that reply is given too. A \
latest message that picks an option from it ("add #1", "the second one", a name from its \
list) asks for that option: the change fits only when it is that option, by name. A \
different place from the list, or one not on it, does not fit, even if it is the same \
kind of thing.

When the latest message doesn't say what it is about and picks nothing (a short \
follow-up like "yes" or "go ahead"), the change fits.

Give a verdict for every change, in order, numbered from 1. Answer with JSON only."""

# The offline (fake) model finds the changes after this marker, one `{n}. {tool}: …` line
# each; the latest user message comes after LATEST_MESSAGE_MARKER, at the very end.
REVIEW_CHANGES_MARKER = "Proposed changes to review:"
# The reply the user is answering comes after this marker, before the changes. qwen3:8b
# proposed a different hotel than the "#1" the user picked from the agent's list, and the
# reviewer, told only "add #1", had nothing to check the card against.
PREVIOUS_REPLY_MARKER = "The assistant's previous reply (what the user is answering):"

# Arguments that show what kind of thing a change really is (a summary only has the title
# and start, and a dinner can be titled like a hotel).
_REVIEW_PLACES = ("place", "from_place", "to_place")
_REVIEW_FIELDS = ("kind", "category", "mode", "carrier", "direction", "start", "end")


def stage_purpose(stage: PlanStage) -> str:
    """What `stage` is for, and which stage owns which kind of request."""
    return f"{STAGE_LABELS[stage]}: {_STAGE_PURPOSES[stage]}. {_STAGE_OWNERSHIP}"


def _change_details(change: ProposedChange) -> str:
    fields = change.arguments
    if change.name == "update_item" and isinstance(fields.get("changes"), dict):
        fields = fields["changes"]
    details: list[str] = []
    for key in _REVIEW_PLACES:
        place = fields.get(key)
        if isinstance(place, dict) and place.get("name"):
            details.append(f"{key}: {place['name']}")
    details += [f"{key}: {fields[key]}" for key in _REVIEW_FIELDS if key in fields]
    return f" ({'; '.join(details)})" if details else ""


def review_line(number: int, change: ProposedChange) -> str:
    return f"{number}. {change.name}: {change.summary}{_change_details(change)}"


def review_messages(
    stage: PlanStage,
    latest: str,
    changes: Sequence[ProposedChange],
    previous_reply: str | None = None,
) -> list[LLMMessage]:
    """The reviewer's request: the stage, the reply the user is answering (if any), the
    numbered changes, and the user's message last (the offline model reads it from after
    the marker)."""
    lines = [review_line(number, change) for number, change in enumerate(changes, start=1)]
    parts = [f"Current stage: {stage_purpose(stage)}"]
    if previous_reply:
        parts.append(f"{PREVIOUS_REPLY_MARKER}\n{previous_reply}")
    parts += [
        f"{REVIEW_CHANGES_MARKER}\n" + "\n".join(lines),
        f"{LATEST_MESSAGE_MARKER}\n{latest}",
    ]
    return [
        LLMMessage(role="system", content=REVIEW_SYSTEM),
        LLMMessage(role="user", content="\n\n".join(parts)),
    ]


# Told to the agent when the reviewer withdrew changes; the offline model looks for it.
REVIEW_FEEDBACK_MARKER = "Your proposed changes were withdrawn:"


def review_feedback(withdrawn: Sequence[tuple[str, str]]) -> str:
    """What the agent is told about withdrawn changes (summary, reason) before it tries
    again."""
    lines = [f"{summary}: {reason}" for summary, reason in withdrawn]
    return (
        f"{REVIEW_FEEDBACK_MARKER}\n{_bullets(lines)}\n\nThey are not on the card. Propose "
        "only what the user asked for in this stage, or explain which stage it belongs to. "
        "Do not propose the withdrawn changes again."
    )


# Shown to the user when withdrawn changes could not be retried.
WITHDRAWN_NOTE = "I withdrew changes that didn't fit your request:"


def withdrawn_note(withdrawn: Sequence[tuple[str, str]]) -> str:
    return f"{WITHDRAWN_NOTE}\n{_bullets([f'{s} — {r}' for s, r in withdrawn])}"


# ---------------------------------------------------------------------------
# Proposal notes (past cards, as the agent sees them in its history)
# ---------------------------------------------------------------------------

# A note runs from "[Proposed changes" to the "]" that ends a line. Lazy and DOTALL, so a
# note spans its bullet lines but stops at its own end; leading blank lines go with it.
_PROPOSAL_NOTE = re.compile(r"\s*\[Proposed changes\b.*?\][ \t]*(?=\n|$)", re.DOTALL)


def proposal_note(status: str, summaries: Sequence[str]) -> str:
    """What a stored reply's proposal looks like to the agent later: the model never sees
    its tool calls again, so without this "approve" has no context."""
    return f"[Proposed changes ({status}):\n{_bullets(summaries)}]"


def strip_proposal_notes(text: str) -> str:
    """Remove notes the model wrote itself, copying its history; they create no card."""
    return _PROPOSAL_NOTE.sub("", text).strip()


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


def _describe_item(item_id: UUID, plan: PlanSnapshot) -> str:
    # The client's warning can be stale (the plan changed since it was computed), so an id
    # that's no longer in the plan is reported rather than treated as an error.
    item = next((item for item in plan.items if item.id == item_id), None)
    if item is None:
        return f"- {item_id}: no longer in the plan"
    return f"- {item_id}: {item.title} ({item.type}, {item.start:%Y-%m-%d %H:%M})"


def fix_request_message(content: str, fix: TimelineWarning, plan: PlanSnapshot) -> str:
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


# The offline (fake) model looks for this to mention the Next button.
STAGE_COMPLETE_MARKER = "stage is now complete"


def approval_applied_message(applied: Sequence[str], completed: StageStatus | None = None) -> str:
    """The agent's latest message after an approval: what was applied (tool results), and
    whether that finished the current stage's checklist."""
    message = (
        f"{APPROVAL_MARKER} These changes are now applied:\n{_bullets(applied)}\n\n"
        "Confirm this in one short sentence and suggest the next step. Propose more "
        "changes only if the user already gave you the details."
    )
    if completed is not None and completed.stage != "review":
        upcoming = STAGES[STAGES.index(completed.stage) + 1]
        message += (
            f"\n\nThe {STAGE_LABELS[completed.stage]} {STAGE_COMPLETE_MARKER}; tell the "
            f"user they can press Next to move on to {STAGE_LABELS[upcoming]}."
        )
    return message


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
