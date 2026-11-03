"""Canned behavior for `LLM_PROVIDER=fake`: lets the app run without Ollama (offline dev and
the Playwright E2E suite). A keyword check stands in for the guardrail, and a few scripted
commands make the agent propose one stage's changes each, so the whole workflow (basics →
lodging → activities → ground → review) works with no model:

- "Set up the trip" → `set_plan_details` (a Paris trip a month from today, two adults);
- "Add a hotel" → `add_lodging` for the whole trip;
- "Add <something>" → `add_activity` (two hours on the trip's second day, or tomorrow);
- "Add transfers" → one `add_transfer` per transfer the ground stage lists as needed.

The fake calls the tool whatever stage the plan is in, so the stage gate's refusal shows.
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from features.chat.prompts import (
    APPROVAL_MARKER,
    FIX_REQUEST_MARKER,
    GAPS_MARKER,
    LATEST_MESSAGE_MARKER,
    REVISE_MARKER,
    STAGE_COMPLETE_MARKER,
    parse_gap_line,
)
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
        "add", "fix", "hi", "hello", "hey", "thanks", "yes", "no", "setup", "transfer",
        "transfers", "taxi",
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

# "Add a visit to the Louvre" -> "visit to the Louvre"
_ADD_COMMAND = re.compile(r"^\s*add\s+(?:an?\s+|the\s+)?(?P<what>\S.*?)[\s.!]*$", re.IGNORECASE)
_SETUP_COMMAND = re.compile(r"^\s*set\s*up\b.*\btrip\b", re.IGNORECASE)
_HOTEL_COMMAND = re.compile(r"^\s*add\s+(?:an?\s+|the\s+)?hotel\b", re.IGNORECASE)
_TRANSFERS_COMMAND = re.compile(r"^\s*add\s+(?:the\s+)?transfers?\b", re.IGNORECASE)

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


def offline_activity(what: str, day: date) -> dict[str, Any]:
    """`add_activity` arguments for "add <what>": two hours that morning, in UTC."""
    title = (what[:1].upper() + what[1:])[:200]
    return {
        "title": title,
        "place": {"name": title},
        "category": "sightseeing",
        "start": datetime.combine(day, time(10)).isoformat(),
        "end": datetime.combine(day, time(12)).isoformat(),
        "timezone": "UTC",
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
    if request.format is not None:  # the only structured-output call is the guardrail
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
