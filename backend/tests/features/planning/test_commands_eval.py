"""How well the command reader turns edit requests into commands on the real model `.env`
selects: `uv run pytest -m live_llm tests/features/planning/test_commands_eval.py -s`. On
Claude this makes real, billed API calls.

Calls `extractor.read_commands` directly (no DB, no turn, no rules) on the Tokyo trip at
the review stage, with economy flights, a midrange hotel and food as the stored search
preferences, and whose itinerary is numbered:

    1 transfer "Narita Express" (home to SFO)    5 activity "Hotel dinner" (Sun 18:00-19:00)
    2 flight "SFO → NRT" (outbound)              6 transfer "Narita Express" (to NRT)
    3 transfer "Narita Express" (NRT to hotel)   7 flight "NRT → SFO" (return)
    4 lodging "Hotel Okura" (May 2-4)            8 transfer "Narita Express" (SFO to home)

Each case says what a good reading is: the right command, a question back when the words
fit more than one item, or no commands at all for a question, off-topic text or an add
(adds come from searches). A case can name the question the planner is waiting on, for
asking for other options than the choices on show. As in the extractor eval, only the pass
rate is asserted and the misses are printed.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import pytest

from features.planning.extractor import CommandReading, read_commands
from features.planning.prompts import command_messages
from features.planning.schemas import (
    ChangeStage,
    Command,
    RedoSearch,
    RemoveItem,
    SearchName,
    SetBasics,
    SetPreferences,
    UpdateItem,
)
from features.plans.schemas import (
    ActivityPreferences,
    FlightPreferences,
    LodgingPreferences,
    PlanPreferences,
    PlanStage,
)
from shared.config import get_settings
from shared.llm.types import LLMClient
from tests.features.plans.builders import complete_trip, snapshot

pytestmark = pytest.mark.live_llm

logger = logging.getLogger(__name__)

# Below this, the command prompt or schema needs work before relying on typed edits.
MIN_PASS_RATE = 0.85

TODAY = date(2027, 3, 9)
HOTEL, DINNER, OUTBOUND, RETURN = 4, 5, 2, 7


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

# A check reads the reading and returns what was wrong with it, or None when it is right.
Check = Callable[[CommandReading], str | None]


@dataclass(frozen=True)
class Case:
    text: str
    check: Check
    pending: str | None = None  # the question the planner is waiting on, if any


def _only(reading: CommandReading) -> Command | str:
    """The reading's single command, or why there isn't one."""
    if reading.status != "command":
        return f"no commands ({reading.status}: {reading.clarification})"
    if len(reading.commands) != 1:
        return f"{len(reading.commands)} commands: {reading.commands}"
    return reading.commands[0]


def removes(ref: int) -> Check:
    def check(reading: CommandReading) -> str | None:
        command = _only(reading)
        if isinstance(command, str):
            return command
        return None if command == RemoveItem(item=ref) else f"read {command}"

    return check


def updates(ref: int, test: Callable[[UpdateItem], bool], what: str) -> Check:
    """An `update_item` on item `ref` that passes `test` (described by `what`)."""

    def check(reading: CommandReading) -> str | None:
        command = _only(reading)
        if isinstance(command, str):
            return command
        if not isinstance(command, UpdateItem) or command.item != ref or not test(command):
            return f"wanted {what}, read {command}"
        return None

    return check


def _wall(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=None)


def starts(ref: int, at: str) -> Check:
    return updates(ref, lambda c: _wall(c.changes.start) == datetime.fromisoformat(at), at)


def ends(ref: int, at: str) -> Check:
    return updates(ref, lambda c: _wall(c.changes.end) == datetime.fromisoformat(at), at)


def sets(test: Callable[[SetBasics], bool], what: str) -> Check:
    def check(reading: CommandReading) -> str | None:
        command = _only(reading)
        if isinstance(command, str):
            return command
        if not isinstance(command, SetBasics) or not test(command):
            return f"wanted {what}, read {command}"
        return None

    return check


def travelers(adults: int, children: int = 0) -> Check:
    def test(command: SetBasics) -> bool:
        group = command.travelers
        return group is not None and (group.adults, group.children) == (adults, children)

    return sets(test, f"{adults} adults, {children} kids")


def stage(action: Literal["back", "go_to", "skip_flights"], to: PlanStage | None = None) -> Check:
    def check(reading: CommandReading) -> str | None:
        command = _only(reading)
        if isinstance(command, str):
            return command
        wanted = ChangeStage(action=action, stage=to)
        return None if command == wanted else f"read {command}"

    return check


def prefers(test: Callable[[SetPreferences], bool], what: str) -> Check:
    def check(reading: CommandReading) -> str | None:
        command = _only(reading)
        if isinstance(command, str):
            return command
        if not isinstance(command, SetPreferences) or not test(command):
            return f"wanted {what}, read {command}"
        return None

    return check


def searches(search: SearchName) -> Check:
    def check(reading: CommandReading) -> str | None:
        command = _only(reading)
        if isinstance(command, str):
            return command
        return None if command == RedoSearch(search=search) else f"read {command}"

    return check


def asks_which(reading: CommandReading) -> str | None:
    if reading.status != "ambiguous":
        return f"read as {reading.status}: {reading.commands}"
    return None


def no_commands(reading: CommandReading) -> str | None:
    """Not an edit, or one the vocabulary can't make: any status but a command."""
    return f"read commands: {reading.commands}" if reading.status == "command" else None


CASES: tuple[Case, ...] = (
    # Removing
    Case("remove the hotel dinner", removes(DINNER)),
    Case("cancel dinner on Sunday", removes(DINNER)),
    Case("I don't need the dinner anymore", removes(DINNER)),
    Case("take the Okura off the plan", removes(HOTEL)),
    Case("drop the return flight", removes(RETURN)),
    Case("get rid of the flight out", removes(OUTBOUND)),
    # Changing an item
    Case("move the dinner to 8pm", starts(DINNER, "2027-05-02T20:00")),
    Case("push dinner back an hour", starts(DINNER, "2027-05-02T19:00")),
    Case("check out of the hotel at 11 instead", ends(HOTEL, "2027-05-04T11:00")),
    Case(
        "rename the dinner to Sushi night",
        updates(DINNER, lambda c: c.changes.title == "Sushi night", "title Sushi night"),
    ),
    Case(
        "add a note to the hotel: ask for a high floor",
        updates(
            HOTEL,
            lambda c: "high floor" in (c.changes.notes or "").casefold(),
            "a note about a high floor",
        ),
    ),
    Case(
        "mark the hotel as confirmed",
        updates(HOTEL, lambda c: c.changes.status == "confirmed", "status confirmed"),
    ),
    # The basics
    Case("make it 3 adults", travelers(3)),
    Case("we're bringing our two kids too", travelers(2, 2)),
    Case(
        "change the budget to 6000 dollars",
        sets(lambda c: c.budget is not None and c.budget.amount == Decimal(6000), "6000"),
    ),
    Case(
        "we're leaving from Oakland now",
        sets(lambda c: c.origin is not None and "oakland" in c.origin.name.casefold(), "Oakland"),
    ),
    Case(
        "change the dates to May 2 to May 5",
        sets(
            lambda c: (
                c.dates is not None
                and (c.dates.start, c.dates.end) == (date(2027, 5, 2), date(2027, 5, 5))
            ),
            "May 2-5",
        ),
    ),
    # Preferences
    Case(
        "we'd rather fly business",
        prefers(lambda c: c.flights is not None and c.flights.cabin == "business", "business"),
    ),
    Case(
        "nonstop flights only",
        prefers(lambda c: c.flights is not None and c.flights.max_stops == "nonstop", "nonstop"),
    ),
    Case(
        "keep hotels under 150 dollars a night",
        prefers(
            lambda c: (
                c.lodging is not None
                and c.lodging.nightly_budget is not None
                and c.lodging.nightly_budget.amount == Decimal(150)
            ),
            "150 a night",
        ),
    ),
    Case(
        "we're into museums too",
        prefers(
            lambda c: (
                c.activities is not None and set(c.activities.interests or []) == {"food", "museum"}
            ),
            "food and museum",
        ),
    ),
    # Other options than the ones on show
    Case("show me some other hotels", searches("lodging"), pending="Pick a place to stay."),
    Case(
        "none of these work, find me other flights",
        searches("flights"),
        pending="Pick your flight home.",
    ),
    # Stages
    Case("go back to lodging", stage("go_to", PlanStage.LODGING)),
    Case("let's redo the flights stage", stage("go_to", PlanStage.FLIGHTS)),
    Case("go back a step", stage("back")),
    Case("skip flights, we're driving", stage("skip_flights")),
    # More than one item fits
    Case("remove the Narita Express", asks_which),
    Case("move the flight to the afternoon", asks_which),
    # Not edits the vocabulary can make
    Case("Is the Okura near Shibuya?", no_commands),
    Case("Write me a poem about cheese", no_commands),
    Case("add a sushi class on Monday", no_commands),
)


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


PREFERENCES = PlanPreferences(
    flights=FlightPreferences(),
    lodging=LodgingPreferences(hotel_class="midrange"),
    activities=ActivityPreferences(interests=["food"]),
)


async def test_command_reader_reads_edit_requests(llm: LLMClient) -> None:
    plan = snapshot(complete_trip(), stage=PlanStage.REVIEW, preferences=PREFERENCES)
    misses: list[str] = []
    for case in CASES:
        messages = command_messages(case.text, plan, plan.items, TODAY, pending=case.pending)
        problem = case.check(await read_commands(llm, messages))
        if problem is not None:
            misses.append(f"{case.text!r}: {problem}")

    passed = len(CASES) - len(misses)
    rate = passed / len(CASES)
    model = get_settings().llm_model
    report = f"Command reader on {model}: {passed}/{len(CASES)} read right ({rate:.0%})"
    # `-s` shows this; the log line keeps it in captured output when the assert fails.
    print("\n" + report + "".join(f"\n  {line}" for line in misses))  # noqa: T201
    logger.info(report)

    assert rate >= MIN_PASS_RATE, (report, misses)
