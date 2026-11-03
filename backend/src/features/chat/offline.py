"""Canned behavior for `LLM_PROVIDER=fake`: lets the app run without Ollama (offline dev and
the Playwright E2E suite). A keyword check stands in for the guardrail, and a few scripted
commands make the agent propose one stage's changes each, so the whole workflow (basics →
lodging → activities → ground → review) works with no model:

- "Set up the trip" → `set_plan_details` (a Paris trip a month from today, two adults);
- "Add a hotel" → `add_lodging` for the whole trip;
- "Add <something>" → `add_activity` (two hours on the trip's second day, or tomorrow);
- "Add transfers" → one `add_transfer` per transfer the ground stage lists as needed;
- "Book dinner at <somewhere>" → a dinner wrongly proposed with `add_lodging` (overnight
  times, so the tools accept it), which the fake reviewer withdraws.

The fake calls the tool whatever stage the plan is in, so the stage gate's refusal shows.
Its reviewer approves everything except lodging proposed for a meal, and a change that
isn't the option the user picked ("add hotel #1") from a numbered list in the previous
reply (the fake always proposes Hotel du Louvre, so a list that puts another hotel first
shows the withdrawal).
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from features.chat.prompts import (
    APPROVAL_MARKER,
    FIX_REQUEST_MARKER,
    GAPS_MARKER,
    LATEST_MESSAGE_MARKER,
    PREVIOUS_REPLY_MARKER,
    REVIEW_CHANGES_MARKER,
    REVIEW_FEEDBACK_MARKER,
    REVISE_MARKER,
    STAGE_COMPLETE_MARKER,
    parse_gap_line,
)
from features.chat.schemas import ChangeVerdict, GuardrailVerdict, ReviewVerdict
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
        "add", "fix", "hi", "hello", "hey", "thanks", "yes", "no", "setup", "transfer",
        "transfers", "taxi", "book", "dinner", "lunch", "breakfast", "meal",
    }
)  # fmt: skip

OFFLINE_AGENT_REPLY = (
    "I'm running in offline mode (LLM_PROVIDER=fake), so I can't plan anything yet. "
    "Start Ollama and set LLM_PROVIDER=ollama to chat for real. To try the planner "
    'anyway, send one command per stage: "Set up the trip", "Add a hotel", "Add a visit '
    'to the Louvre" or "Add transfers".'
)
OFFLINE_REDIRECT_REPLY = "That's outside what I can help with, so let's get back to your trip!"
OFFLINE_PROPOSED_REPLY = (
    "Here's my suggestion — it's on the card above. Approve to add it to your itinerary."
)
OFFLINE_ADDED_REPLY = "Done! It's on your itinerary. Click the card on the timeline for details."
OFFLINE_STAGE_DONE_REPLY = "That completes this stage: press Next when you're ready."
OFFLINE_NOT_APPLIED_REPLY = "Sorry, that couldn't be applied: the plan changed in the meantime."
OFFLINE_FIX_REPLY = (
    "Here's what I'd change to fix that (offline mode, so this is only an example): move "
    "one of the items to a free slot. Say 'add <something>' and I'll propose it."
)
OFFLINE_NO_GAPS_REPLY = "Every change of place already has a transfer, so there's nothing to add."
OFFLINE_WITHDRAWN_REPLY = (
    "Sorry, scratch that: a dinner isn't somewhere to stay, so I withdrew it. Meals and "
    "restaurants belong to the activities stage."
)
# Worded so the agent's claim check ("I proposed …") and promised-search check ("I'll look
# it up") don't fire on it.
OFFLINE_WRONG_PICK_REPLY = (
    "Sorry, scratch that: the place on the card wasn't the one you picked, so I withdrew "
    "it. Tell me its name and I'll propose that one."
)
# Why the fake reviewer rejects lodging proposed for a meal.
OFFLINE_REVIEW_REASON = "the user asked for a meal, not somewhere to stay"
# Why it rejects a change that isn't the option the user picked ("… #1, Hotel Granbell").
OFFLINE_PICK_REASON = "the user picked"

# "Add a visit to the Louvre" -> "visit to the Louvre"
_ADD_COMMAND = re.compile(r"^\s*add\s+(?:an?\s+|the\s+)?(?P<what>\S.*?)[\s.!]*$", re.IGNORECASE)
_SETUP_COMMAND = re.compile(r"^\s*set\s*up\b.*\btrip\b", re.IGNORECASE)
_HOTEL_COMMAND = re.compile(r"^\s*add\s+(?:an?\s+|the\s+)?hotel\b", re.IGNORECASE)
_TRANSFERS_COMMAND = re.compile(r"^\s*add\s+(?:the\s+)?transfers?\b", re.IGNORECASE)
# "Book dinner at a fado house" -> " at a fado house"
_DINNER_COMMAND = re.compile(r"^\s*book\s+(?:a\s+)?dinner\b(?P<rest>.*?)[\s.!]*$", re.IGNORECASE)
# What the fake reviewer takes for a request for a meal.
_MEAL_WORDS = re.compile(r"\b(?:dinner|restaurant|meal|lunch|breakfast)\b", re.IGNORECASE)
# A change line in the review request: "1. add_lodging: Add lodging …".
_REVIEW_LINE = re.compile(r"^(?P<number>\d+)\. (?P<tool>\w+):(?P<rest>.*)$", re.MULTILINE)
# A pick from a numbered list in the previous reply: "#1", "number 2", "the second one".
_PICK = re.compile(
    r"(?:#|\b(?:number|option|no\.?)\s*)(?P<digit>\d+)"
    r"|\b(?P<word>first|second|third|fourth|fifth)\b",
    re.IGNORECASE,
)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
# An entry of that list: "1. **Hotel Granbell** - 3 stars" -> "Hotel Granbell". The name
# ends at the first dash, colon, bracket or markdown emphasis.
_LIST_ITEM = re.compile(
    r"^\s*(?:\*\*)?(?P<number>\d+)[.)]\s*\**(?P<name>[^*\n(:\u2013\u2014-]+)", re.MULTILINE
)

# The plan JSON in the system prompt is compact (`"start_date":"2027-05-01"`).
_PLAN_DATE = re.compile(r'"(?P<field>start_date|end_date)":"(?P<day>\d{4}-\d{2}-\d{2})"')

# How far ahead "Set up the trip" puts the trip, and how long it is.
_TRIP_LEAD = timedelta(days=30)
_TRIP_LENGTH = timedelta(days=3)
_PARIS = {"name": "Paris", "city": "Paris", "country": "France"}
_PARIS_TZ = "Europe/Paris"
# A home for the trip when the user set none, so the basics checklist can be completed.
_DEFAULT_HOME = {"name": "Philadelphia", "city": "Philadelphia", "iata": "PHL"}

_TRANSFER_TIME = timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _latest_user_text(request: FakeRequest) -> str:
    content = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    content = content.rsplit(LATEST_MESSAGE_MARKER, 1)[-1]
    if REVISE_MARKER in content:
        # "Other…": just what the user typed, without the framing around it.
        content = content.split(REVISE_MARKER, 1)[1].split("\n\n", 1)[0]
    return content.strip()


def _system_prompt(request: FakeRequest) -> str:
    return next((m.content for m in request.messages if m.role == "system"), "")


def _trip_dates(system: str) -> tuple[date, date] | None:
    found = {
        m.group("field"): date.fromisoformat(m.group("day")) for m in _PLAN_DATE.finditer(system)
    }
    if "start_date" in found and "end_date" in found:
        return found["start_date"], found["end_date"]
    return None


def offline_trip(today: date, has_origin: bool) -> dict[str, Any]:
    """`set_plan_details` arguments for "Set up the trip"."""
    start = today + _TRIP_LEAD
    arguments: dict[str, Any] = {
        "title": "Paris getaway",
        "destination": _PARIS,
        "start_date": start.isoformat(),
        "end_date": (start + _TRIP_LENGTH).isoformat(),
        "travelers": {"adults": 2},
    }
    if not has_origin:
        arguments["origin"] = _DEFAULT_HOME
    return arguments


def offline_hotel(trip: tuple[date, date]) -> dict[str, Any]:
    """`add_lodging` arguments covering every night of the trip."""
    start, end = trip
    return {
        "title": "Hotel du Louvre",
        "place": {"name": "Hotel du Louvre", "city": "Paris"},
        "start": datetime.combine(start, time(15)).isoformat(),
        "end": datetime.combine(end, time(11)).isoformat(),
        "timezone": _PARIS_TZ,
    }


def offline_dinner(rest: str, day: date) -> dict[str, Any]:
    """`add_lodging` arguments for "book dinner <rest>": the small-model mistake the
    reviewer exists for. Overnight times, so the tools' own lodging check lets it through."""
    title = f"Dinner{rest}"[:200]
    return {
        "title": title,
        "place": {"name": title, "city": "Paris"},
        "kind": "other",
        "start": datetime.combine(day, time(19)).isoformat(),
        "end": datetime.combine(day + timedelta(days=1), time(9)).isoformat(),
        "timezone": _PARIS_TZ,
    }


def _picked_option(latest: str, previous_reply: str) -> tuple[int, str] | None:
    """The (number, name) the user picked from the numbered list in `previous_reply`
    ("add #1", "the second one"), or None when the message picks nothing or the reply
    has no such entry."""
    pick = _PICK.search(latest)
    if pick is None:
        return None
    number = int(pick.group("digit") or _ORDINALS[pick.group("word").lower()])
    for item in _LIST_ITEM.finditer(previous_reply):
        if int(item.group("number")) == number:
            return number, item.group("name").strip()
    return None


def offline_review(request: FakeRequest) -> ReviewVerdict:
    """Reject every add_lodging change when the user asked for a meal, and every change
    that isn't the option the user picked from the previous reply; approve the rest."""
    latest = _latest_user_text(request)
    content = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    previous = content.partition(PREVIOUS_REPLY_MARKER)[2].partition(REVIEW_CHANGES_MARKER)[0]
    changes = content.split(REVIEW_CHANGES_MARKER, 1)[-1].split(LATEST_MESSAGE_MARKER, 1)[0]
    meal = _MEAL_WORDS.search(latest) is not None
    picked = _picked_option(latest, previous)
    verdicts: list[ChangeVerdict] = []
    for line in _REVIEW_LINE.finditer(changes):
        number = int(line.group("number"))
        if meal and line.group("tool") == "add_lodging":
            verdicts.append(
                ChangeVerdict(index=number, fits_request=False, reason=OFFLINE_REVIEW_REASON)
            )
        elif picked is not None and picked[1].lower() not in line.group("rest").lower():
            reason = f"{OFFLINE_PICK_REASON} #{picked[0]}, {picked[1]}"
            verdicts.append(ChangeVerdict(index=number, fits_request=False, reason=reason))
    return ReviewVerdict(verdicts=verdicts)


def offline_activity(what: str, day: date) -> dict[str, Any]:
    """`add_activity` arguments for "add <what>": two hours that morning, Paris time (the
    fake's trip is always Paris; in UTC, the taxis "Add transfers" proposes around it would
    fail the traveller's-timezone check once a taxi has brought them into Paris)."""
    title = (what[:1].upper() + what[1:])[:200]
    return {
        "title": title,
        "place": {"name": title},
        "category": "sightseeing",
        "start": datetime.combine(day, time(10)).isoformat(),
        "end": datetime.combine(day, time(12)).isoformat(),
        "timezone": _PARIS_TZ,
    }


def offline_transfers(system: str) -> list[dict[str, Any]]:
    """`add_transfer` arguments, a taxi for each gap the ground stage lists."""
    if GAPS_MARKER not in system:
        return []
    transfers: list[dict[str, Any]] = []
    for line in system.split(GAPS_MARKER, 1)[1].splitlines():
        gap = parse_gap_line(line)
        if gap is None:
            continue
        leave = datetime.fromisoformat(gap["leave"]) if gap["leave"] else None
        arrive = datetime.fromisoformat(gap["arrive"]) if gap["arrive"] else None
        # Fit a half-hour ride into the window: right after leaving, or just before
        # arriving when leaving home (which has no time of its own).
        if leave is not None:
            start = leave
            end = start + _TRANSFER_TIME
            if arrive is not None and arrive > start:
                end = min(end, arrive)
        elif arrive is not None:
            start, end = arrive - _TRANSFER_TIME, arrive
        else:
            continue
        transfers.append(
            {
                "title": f"Taxi to {gap['to_name']}"[:200],
                "mode": "taxi",
                "from_place": {"name": gap["from_name"]},
                "to_place": {"name": gap["to_name"]},
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timezone": gap["timezone"],
            }
        )
    return transfers


# ---------------------------------------------------------------------------
# Fallback reply
# ---------------------------------------------------------------------------


def _command_reply(latest: str, system: str) -> LLMMessage | None:
    """The tool calls for a scripted command, or None if `latest` isn't one."""
    today = date.today()
    trip = _trip_dates(system)
    if _SETUP_COMMAND.match(latest):
        return tool_calls(("set_plan_details", offline_trip(today, '"origin":' in system)))
    if _HOTEL_COMMAND.match(latest):
        stay = trip or (today + _TRIP_LEAD, today + _TRIP_LEAD + _TRIP_LENGTH)
        return tool_calls(("add_lodging", offline_hotel(stay)))
    dinner = _DINNER_COMMAND.match(latest)
    if dinner is not None:
        day = trip[0] if trip else today + _TRIP_LEAD
        return tool_calls(("add_lodging", offline_dinner(dinner.group("rest"), day)))
    if _TRANSFERS_COMMAND.match(latest):
        transfers = offline_transfers(system)
        if not transfers:
            return text(OFFLINE_NO_GAPS_REPLY)
        return tool_calls(*(("add_transfer", arguments) for arguments in transfers))
    command = _ADD_COMMAND.match(latest)
    if command is not None:
        day = trip[0] + timedelta(days=1) if trip else today + timedelta(days=1)
        return tool_calls(("add_activity", offline_activity(command.group("what"), day)))
    return None


def offline_reply(request: FakeRequest) -> LLMMessage:
    # Structured-output calls are told apart by their schema's fields (inlined schemas have
    # no title): the reviewer's has `verdicts`, the guardrail's `on_topic`.
    properties = (request.format or {}).get("properties", {})
    if "verdicts" in properties:
        return json_reply(offline_review(request))
    if request.format is not None:
        words = set(re.findall(r"[a-z]+", _latest_user_text(request).lower()))
        on_topic = bool(words & _TRAVEL_WORDS)
        return json_reply(
            GuardrailVerdict(
                on_topic=on_topic, confidence=0.9, topic="travel" if on_topic else "other"
            )
        )
    # The agent always offers tools (web_search at least); the redirect never does.
    if not request.tools:
        return text(OFFLINE_REDIRECT_REPLY)
    # Second agent round: the change is staged, so wrap up. (Failures report "Error: …".)
    if request.messages and request.messages[-1].role == "tool":
        result = request.messages[-1].content
        if result.startswith("Error"):
            return text(f"I couldn't add that. {result}")
        return text(OFFLINE_PROPOSED_REPLY)
    # The reviewer withdrew the change: acknowledge it rather than proposing it again.
    last = request.messages[-1] if request.messages else None
    if last is not None and last.role == "system" and REVIEW_FEEDBACK_MARKER in last.content:
        if OFFLINE_PICK_REASON in last.content:
            return text(OFFLINE_WRONG_PICK_REPLY)
        return text(OFFLINE_WITHDRAWN_REPLY)
    latest = _latest_user_text(request)
    # The turn after the user approved a proposal card.
    if APPROVAL_MARKER in latest:
        if "no longer" in latest:
            return text(OFFLINE_NOT_APPLIED_REPLY)
        if STAGE_COMPLETE_MARKER in latest:
            return text(f"{OFFLINE_ADDED_REPLY} {OFFLINE_STAGE_DONE_REPLY}")
        return text(OFFLINE_ADDED_REPLY)
    # Offline, a "Fix it" request only describes a fix (it can't work one out).
    if FIX_REQUEST_MARKER in latest:
        return text(OFFLINE_FIX_REPLY)
    return _command_reply(latest, _system_prompt(request)) or text(OFFLINE_AGENT_REPLY)
