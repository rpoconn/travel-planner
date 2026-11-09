"""The planning flow's prompts: the slot extractor, the pick reader and the command reader
(structured output), and the short answer to a question (plain streamed text that is never
parsed)."""

from collections.abc import Sequence
from datetime import date, datetime

from features.itinerary.schemas import FlightItem, ItineraryItem, TransferItem
from features.planning.schemas import MAX_COMMANDS, OfferChoice, RejectedChange
from features.planning.slots import SlotSpec
from features.plans.schemas import PlanSnapshot, PlanStage
from features.plans.stages import STAGE_LABELS
from shared.llm.types import LLMMessage

# The first words of the question-answering prompt; the offline fake recognises it by them.
QA_INTRO = "You answer one question from someone planning a trip"

# What counts as an edit, for the readers that hand edits on to the command reader.
_EDITS = (
    "remove or move something already in the trip, change its details, change the dates, "
    "travelers, destination, starting point or budget, change what kind of flights, place to "
    "stay or things to do they want, see other options than the ones offered, go back to an "
    "earlier stage, or skip flights"
)


# ---------------------------------------------------------------------------
# The trip
# ---------------------------------------------------------------------------


def trip_summary(plan: PlanSnapshot) -> str:
    """What's known about the trip so far, one fact per line."""
    facts = [f"Planning stage: {STAGE_LABELS[plan.stage]}"]
    if plan.destination is not None:
        facts.append(f"Destination: {plan.destination.name}")
    if plan.start_date is not None and plan.end_date is not None:
        facts.append(f"Dates: {plan.start_date.isoformat()} to {plan.end_date.isoformat()}")
    if plan.travelers is not None:
        facts.append(f"Travelers: {plan.travelers.adults} adults, {plan.travelers.children} kids")
    if plan.origin is not None:
        facts.append(f"Leaving from: {plan.origin.name}")
    return "\n".join(facts)


# ---------------------------------------------------------------------------
# The slot extractor and the pick reader
# ---------------------------------------------------------------------------


def extractor_messages(
    spec: SlotSpec, content: str, plan: PlanSnapshot, today: date
) -> list[LLMMessage]:
    system = (
        "You read one message from someone planning a trip and decide whether it answers "
        f'this question: "{spec.prompt}"\n'
        f"An answer gives: {spec.extractor_hint}\n"
        f"Today is {today:%A} {today.isoformat()}.\n"
        f"The trip so far:\n{trip_summary(plan)}\n\n"
        "Reply with JSON:\n"
        '- "status": "answered" and the "value", when the message gives the answer. Work '
        "out relative dates (next weekend, in June) from today.\n"
        '- "status": "command" and no value, when instead of answering, the message asks to '
        f"{_EDITS}.\n"
        '- "status": "question" and no value, when the message asks something instead.\n'
        '- "status": "unrelated" and no value, for anything else.\n'
        "Only use what the message says; never guess a value it doesn't give."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=content)]


def _option_line(index: int, action: OfferChoice) -> str:
    candidate = action.candidates[index]
    parts = [candidate.title, candidate.subtitle, *candidate.details, *candidate.warnings]
    if candidate.price is not None:
        parts.append(f"{candidate.price.amount:,.0f} {candidate.price.currency}")
    return f'- id "{candidate.id}" (option {index + 1}): ' + " | ".join(p for p in parts if p)


def pick_messages(action: OfferChoice, content: str, plan: PlanSnapshot) -> list[LLMMessage]:
    options = "\n".join(_option_line(index, action) for index in range(len(action.candidates)))
    if action.max_picks > 1:
        how_many = f"between {action.min_picks} and {action.max_picks} of them"
    else:
        how_many = "one of them"
    system = (
        "You read one message from someone planning a trip and decide whether it picks from "
        f'these options, offered for "{action.prompt}" ({how_many}):\n{options}\n'
        f"The trip so far:\n{trip_summary(plan)}\n\n"
        "Reply with JSON:\n"
        '- "status": "answered" and the "candidate_ids" picked, when the message picks '
        '(by position like "the second one", by airline, time, price, or name).\n'
        '- "status": "command" and no ids, when instead of picking, the message asks to '
        f"{_EDITS}.\n"
        '- "status": "question" and no ids, when the message asks something instead.\n'
        '- "status": "unrelated" and no ids, for anything else.\n'
        "Only use ids from the list, and only pick what the message asks for."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=content)]


# ---------------------------------------------------------------------------
# The command reader
# ---------------------------------------------------------------------------


def _time(value: datetime) -> str:
    # Wall-clock time in the item's own zone, in the form `ItemEdits` reads back.
    return f"{value:%a %Y-%m-%dT%H:%M}"


def item_line(number: int, item: ItineraryItem) -> str:
    """One itinerary item as the command reader sees it, under the number `commands` reads
    the reference back with."""
    zone = item.timezone
    if item.end_timezone is not None and item.end_timezone != item.timezone:
        zone = f"{item.timezone} to {item.end_timezone}"
    if isinstance(item, FlightItem | TransferItem):
        where = f"{item.from_place.name} to {item.to_place.name}"
    else:
        where = item.place.name
    parts = [
        f"{number}. {item.type} “{item.title}”",
        f"{_time(item.start)} to {_time(item.end)} ({zone})",
        where,
    ]
    if item.status == "cancelled":
        parts.append("removed")
    return " | ".join(parts)


def preferences_summary(plan: PlanSnapshot) -> str:
    """The stored search preferences, one kind per line, in the fields a `set_preferences`
    command names; empty when none are set."""
    stored = plan.preferences
    lines = [
        f"- {name}: {value.model_dump_json(exclude={'outbound_token'})}"
        for name, value in (
            ("flights", stored.flights),
            ("lodging", stored.lodging),
            ("activities", stored.activities),
        )
        if value is not None
    ]
    return "Search preferences:\n" + "\n".join(lines) + "\n" if lines else ""


def _stage_name(stage: PlanStage) -> str:
    label = STAGE_LABELS[stage]
    return stage.value if label == stage.value else f"{stage.value} ({label})"


def command_messages(
    content: str,
    plan: PlanSnapshot,
    items: Sequence[ItineraryItem],
    today: date,
    *,
    pending: str | None = None,
    history: Sequence[LLMMessage] = (),
) -> list[LLMMessage]:
    """The command reader's prompt. `items` is the numbered list (`commands.numbered_items`),
    `pending` the planner's open question if there is one, and `history` the exchange this
    message answers (a request and the clarification asked back), if any."""
    if items:
        lines = (item_line(number, item) for number, item in enumerate(items, start=1))
        itinerary = "The itinerary, numbered:\n" + "\n".join(lines)
    else:
        itinerary = "The itinerary is empty."
    asking = f'The planner is waiting for an answer to: "{pending}"\n' if pending else ""
    stages = ", ".join(_stage_name(stage) for stage in PlanStage)
    system = (
        "You read one message from someone planning a trip and turn the changes it asks for "
        "into commands.\n"
        f"Today is {today:%A} {today.isoformat()}.\n"
        f"The trip so far:\n{trip_summary(plan)}\n"
        f"{preferences_summary(plan)}"
        f"{asking}"
        f"Stages, in order: {stages}. The plan is at {plan.stage.value}.\n"
        f"{itinerary}\n\n"
        "Reply with JSON:\n"
        '- "status": "command" and the "commands", in the order to make them, when the '
        f"message asks to {_EDITS}.\n"
        '- "status": "ambiguous" and a short "clarification" question naming the options, '
        "when it asks for a change but more than one item could match, or the change itself "
        "is unclear.\n"
        '- "status": "question" and no commands, when it asks something instead.\n'
        '- "status": "unrelated" and no commands, for anything else.\n'
        "Rules:\n"
        "- Point at an item only by its number in the list. Never add items: flights, stays "
        "and activities come from searches.\n"
        "- Removing an item is remove_item. Never change a cost, place or airport.\n"
        "- Item times are local wall-clock times in the item's timezone, like "
        "2027-05-02T19:30. When moving an item, give both start and end, and keep its "
        "length unless told otherwise.\n"
        "- set_preferences changes what a stage searches for, giving only the fields to "
        "change (interests: the whole new list). Choices on show for that stage are searched "
        "for again by themselves.\n"
        "- redo_search finds other options than the choices the planner is waiting on; use "
        "it only for those. A stay or activity already in the trip is replaced by removing "
        "it.\n"
        "- go_to only goes back to an earlier stage; moving forward isn't a command.\n"
        f"- At most {MAX_COMMANDS} commands, and only the changes the message asks for."
    )
    return [
        LLMMessage(role="system", content=system),
        *history,
        LLMMessage(role="user", content=content),
    ]


def repair_messages(
    messages: Sequence[LLMMessage], reply: str, rejected: Sequence[RejectedChange]
) -> list[LLMMessage]:
    """The command prompt again, with the reader's earlier reply and the rules its commands
    broke, so it can correct them once (a time in the wrong zone, a missing end)."""
    reasons = "\n".join(f"- {change.summary}: {change.reason}" for change in rejected)
    note = (
        f"Those commands break the trip's rules, so nothing was changed:\n{reasons}\n"
        "Reply again with corrected commands for what the message asked, or with status "
        '"unrelated" if it can\'t be done within the rules.'
    )
    return [
        *messages,
        LLMMessage(role="assistant", content=reply),
        LLMMessage(role="user", content=note),
    ]


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def question_messages(
    plan: PlanSnapshot, content: str, pending: SlotSpec | None
) -> list[LLMMessage]:
    asking = f'\nThe planner is waiting for an answer to: "{pending.prompt}"' if pending else ""
    system = (
        f"{QA_INTRO}, in two or three sentences.\n"
        f"The trip so far:\n{trip_summary(plan)}{asking}\n\n"
        "Only answer questions about this trip or about travel. For anything else, say in "
        "one sentence that you can only help plan the trip. Never say you added, booked or "
        "changed anything: answering a question changes nothing."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=content)]
