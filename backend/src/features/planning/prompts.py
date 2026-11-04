"""The planning flow's prompts: the slot extractor and the pick reader (structured output),
and the short answer to a question (plain streamed text that is never parsed)."""

from datetime import date

from features.planning.schemas import OfferChoice
from features.planning.slots import SlotSpec
from features.plans.schemas import PlanSnapshot
from features.plans.stages import STAGE_LABELS
from shared.llm.types import LLMMessage

# The first words of the question-answering prompt; the offline fake recognises it by them.
QA_INTRO = "You answer one question from someone planning a trip"


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
        '- "status": "question" and no ids, when the message asks something instead.\n'
        '- "status": "unrelated" and no ids, for anything else.\n'
        "Only use ids from the list, and only pick what the message asks for."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=content)]


def question_messages(
    plan: PlanSnapshot, content: str, pending: SlotSpec | None
) -> list[LLMMessage]:
    asking = f'\nThe planner is waiting for an answer to: "{pending.prompt}"' if pending else ""
    system = (
        f"{QA_INTRO}, in two or three sentences.\n"
        f"The trip so far:\n{trip_summary(plan)}{asking}\n\n"
        "Only answer questions about this trip or about travel. For anything else, say in "
        "one sentence that you can only help plan the trip. Never say you added, booked or "
        "changed anything: the plan only changes through the planner's forms and choices."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=content)]
