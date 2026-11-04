"""Builders for plan snapshots, for stage and planning engine tests (no database)."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from features.itinerary.schemas import ItineraryItem
from features.plans.schemas import PlanDetails, PlanSnapshot
from features.plans.stages import plan_progress
from shared.domain import Place
from tests.features.itinerary.factories import (
    HOME,
    LA_TZ,
    NRT,
    OKURA,
    PLAN_ID,
    SFO,
    TOKYO_TZ,
    activity,
    flight,
    lodging,
    transfer,
)

CREATED = datetime(2026, 10, 1, tzinfo=UTC)


def details(**overrides: Any) -> PlanDetails:
    """A plan whose basics are complete: May 1-4 2027 in Tokyo, from HOME, two adults."""
    fields: dict[str, Any] = {
        "id": PLAN_ID,
        "user_id": uuid4(),
        "title": "Tokyo",
        "status": "draft",
        "stage": "basics",
        "flights_skipped": False,
        "origin": HOME,
        "destination": Place(name="Tokyo"),
        "start_date": date(2027, 5, 1),
        "end_date": date(2027, 5, 4),
        "travelers": {"adults": 2},
        "budget": None,
        "limits": [],
        "notes": "",
        "version": 1,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return PlanDetails.model_validate({**fields, **overrides})


def snapshot(items: Sequence[ItineraryItem] = (), **overrides: Any) -> PlanSnapshot:
    plan = details(**overrides)
    progress = plan_progress(plan, items)
    return PlanSnapshot.model_validate(
        {**dict(plan), "items": list(items), "stages": progress.stages, "gaps": progress.gaps}
    )


def outbound_flight() -> ItineraryItem:
    return flight("2027-05-01T11:00", "2027-05-02T14:30")


def return_flight() -> ItineraryItem:
    return flight(
        "2027-05-04T17:00",
        "2027-05-04T10:00",
        from_place=NRT,
        to_place=SFO,
        timezone=TOKYO_TZ,
        end_timezone=LA_TZ,
    ).model_copy(update={"direction": "return"})


def complete_trip() -> list[ItineraryItem]:
    """Every stage done: flights both ways, three nights, a visit, and every transfer."""
    return [
        transfer(
            "2027-05-01T07:00", "2027-05-01T08:00", from_place=HOME, to_place=SFO, timezone=LA_TZ
        ),
        outbound_flight(),
        transfer("2027-05-02T15:00", "2027-05-02T16:30", from_place=NRT, to_place=OKURA),
        # Arrives May 2 local, so the night of May 1 is spent on the plane.
        lodging("2027-05-02T17:00", "2027-05-04T09:00"),
        activity("2027-05-02T18:00", "2027-05-02T19:00", place=OKURA, title="Hotel dinner"),
        transfer("2027-05-04T09:00", "2027-05-04T10:30", from_place=OKURA, to_place=NRT),
        return_flight(),
        transfer(
            "2027-05-04T11:00", "2027-05-04T12:00", from_place=SFO, to_place=HOME, timezone=LA_TZ
        ),
    ]
