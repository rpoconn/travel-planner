"""Input slot answers: check one against its slot, describe it for the transcript, and write
it to the plan. The same path serves a submitted form and a value the extractor read from
free text, so both are validated alike. No model calls here.
"""

import logging
from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from features.planning.schemas import BudgetAnswer, DatesAnswer, DestinationAnswer, OriginAnswer
from features.planning.slots import SLOTS
from features.plans import service as plans_service
from features.plans.models import Plan
from features.plans.schemas import (
    STOP_LABELS,
    ActivityPreferences,
    FlightPreferences,
    FlightPreferencesAnswer,
    LodgingPreferences,
    PlanPreferences,
    PlanSnapshot,
    PlanUpdate,
    SlotName,
)
from shared.domain import Money, Place, Travelers

logger = logging.getLogger(__name__)

# The title a new plan gets; answering the destination replaces it with a real one.
_DEFAULT_TITLE = "Untitled trip"


class AnswerError(ValueError):
    """The answer doesn't fit its slot. The message is shown to the user."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _first_error(exc: ValidationError) -> str:
    """One readable line from a validation error, e.g. "adults: must be at least 1"."""
    error = exc.errors(include_url=False)[0]
    message = str(error["msg"])
    if error["type"] == "value_error":
        # Our own validators' messages are already sentences for people.
        return message.removeprefix("Value error, ")
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {message}" if location else message


def parse_answer(slot: SlotName, value: Mapping[str, Any], today: date) -> BaseModel:
    """`value` as `slot`'s value model, or `AnswerError` saying what's wrong with it."""
    spec = SLOTS[slot]
    if spec.value_model is None:
        raise AnswerError("That question is answered by picking from the list")
    try:
        answer = spec.value_model.model_validate(value)
    except ValidationError as exc:
        raise AnswerError(_first_error(exc)) from exc
    if isinstance(answer, DatesAnswer) and answer.dates.start < today:
        raise AnswerError("The trip can't start in the past")
    return answer


# ---------------------------------------------------------------------------
# Transcript text: what the user's answer reads as in the conversation
# ---------------------------------------------------------------------------


def _place(place: Place) -> str:
    return ", ".join(part for part in (place.name, place.country) if part)


def _money(money: Money) -> str:
    return f"{money.amount:,.2f}".removesuffix(".00") + f" {money.currency}"


def _count(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _day(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def _flight_preferences(answer: FlightPreferencesAnswer) -> str:
    parts = [f"{answer.cabin.capitalize()} cabin", STOP_LABELS[answer.max_stops]]
    if answer.depart_window != "any":
        parts.append(f"{answer.depart_window} departures")
    return ", ".join(parts)


def _lodging_preferences(answer: LodgingPreferences) -> str:
    parts = [f"{answer.hotel_class.capitalize()} {answer.kind}"]
    if answer.nightly_budget is not None:
        parts.append(f"up to {_money(answer.nightly_budget)} a night")
    if answer.neighborhood:
        parts.append(f"in {answer.neighborhood}")
    return ", ".join(parts)


def _activity_preferences(answer: ActivityPreferences) -> str:
    interests = ", ".join(interest.capitalize() for interest in answer.interests)
    return f"{interests}; {answer.dietary}" if answer.dietary else interests


def describe_answer(answer: BaseModel) -> str:
    """The answer as one line of the transcript, e.g. "May 1, 2027 to May 8, 2027"."""
    match answer:
        case DestinationAnswer():
            return _place(answer.destination)
        case DatesAnswer():
            return f"{_day(answer.dates.start)} to {_day(answer.dates.end)}"
        case Travelers():
            people = [_count(answer.adults, "adult", "adults")]
            if answer.children:
                people.append(_count(answer.children, "child", "children"))
            return ", ".join(people)
        case OriginAnswer():
            return f"From {_place(answer.origin)}"
        case BudgetAnswer():
            return f"A budget of {_money(answer.budget)}"
        case FlightPreferencesAnswer():
            return _flight_preferences(answer)
        case LodgingPreferences():
            return _lodging_preferences(answer)
        case ActivityPreferences():
            return _activity_preferences(answer)
    return str(answer)


SKIPPED_TEXT = "Skip"


# ---------------------------------------------------------------------------
# Writing the answer to the plan
# ---------------------------------------------------------------------------


def _plan_update(plan: PlanSnapshot, answer: BaseModel) -> PlanUpdate | None:
    """The plan fields a basics answer sets; None for a preferences answer."""
    match answer:
        case DestinationAnswer():
            if plan.title == _DEFAULT_TITLE:
                title = f"Trip to {answer.destination.name}"
                return PlanUpdate(destination=answer.destination, title=title)
            return PlanUpdate(destination=answer.destination)
        case DatesAnswer():
            return PlanUpdate(start_date=answer.dates.start, end_date=answer.dates.end)
        case Travelers():
            return PlanUpdate(travelers=answer)
        case OriginAnswer():
            return PlanUpdate(origin=answer.origin)
        case BudgetAnswer():
            return PlanUpdate(budget=answer.budget)
    return None


def _with_preference(preferences: PlanPreferences, answer: BaseModel) -> PlanPreferences:
    match answer:
        case FlightPreferencesAnswer():
            # A new answer starts the flight search over, so no outbound token carries on.
            flights = FlightPreferences(**answer.model_dump())
            return preferences.model_copy(update={"flights": flights})
        case LodgingPreferences():
            return preferences.model_copy(update={"lodging": answer})
        case ActivityPreferences():
            return preferences.model_copy(update={"activities": answer})
    raise AnswerError(f"{type(answer).__name__} isn't an answer the plan stores")


async def apply_answer(
    session: AsyncSession, user_id: UUID, plan: PlanSnapshot, answer: BaseModel
) -> Plan:
    """Write a parsed answer to the plan (basics to its fields, the rest to preferences)."""
    update = _plan_update(plan, answer)
    if update is not None:
        try:
            return await plans_service.update_plan(session, user_id, plan.id, update)
        except plans_service.InvalidPlanError as exc:
            raise AnswerError(str(exc)) from exc
    preferences = _with_preference(plan.preferences, answer)
    return await plans_service.update_preferences(session, user_id, plan.id, preferences)


async def skip_slot(
    session: AsyncSession, user_id: UUID, plan: PlanSnapshot, slot: SlotName
) -> Plan:
    """Record that the user declined an optional slot, so it isn't asked again."""
    if not SLOTS[slot].optional:
        raise AnswerError("That question can't be skipped")
    declined = plan.preferences.declined
    preferences = plan.preferences.model_copy(
        update={"declined": declined if slot in declined else [*declined, slot]}
    )
    logger.info("Plan %s declined slot %s", plan.id, slot)
    return await plans_service.update_preferences(session, user_id, plan.id, preferences)
