"""The offline (fake) model's scripted commands, used by the E2E suite."""

from datetime import date
from typing import Any
from uuid import uuid4

from features.chat.offline import offline_transfers, offline_trip
from features.chat.prompts import agent_system_prompt
from features.itinerary.gaps import find_gaps
from features.itinerary.schemas import ItineraryItem, TransferItem
from features.plans.schemas import PlanDetails, TravelPlan
from features.plans.stages import plan_progress
from tests.features.itinerary.factories import HOME, PLAN_ID, flight, lodging

CREATED = date(2026, 10, 1)


def _ground_plan(items: list[ItineraryItem]) -> TravelPlan:
    details = PlanDetails.model_validate(
        {
            "id": PLAN_ID,
            "user_id": uuid4(),
            "title": "Tokyo",
            "status": "draft",
            "stage": "ground",
            "flights_skipped": False,
            "origin": HOME,
            "destination": {"name": "Tokyo"},
            "start_date": date(2027, 5, 1),
            "end_date": date(2027, 5, 6),
            "travelers": {"adults": 2},
            "budget": None,
            "limits": [],
            "notes": "",
            "version": 1,
            "created_at": f"{CREATED}T00:00:00Z",
            "updated_at": f"{CREATED}T00:00:00Z",
        }
    )
    progress = plan_progress(details, items)
    return TravelPlan.model_validate(
        {**dict(details), "items": items, "stages": progress.stages, "gaps": progress.gaps}
    )


def _as_item(arguments: dict[str, Any]) -> TransferItem:
    return TransferItem.model_validate(
        {**arguments, "type": "transfer", "id": uuid4(), "plan_id": PLAN_ID, "created_by": "user"}
    )


def test_offline_transfers_close_every_gap_in_the_prompt() -> None:
    items: list[ItineraryItem] = [
        flight("2027-05-01T11:00", "2027-05-02T14:30"),
        lodging("2027-05-02T17:00", "2027-05-06T11:00"),
    ]
    prompt = agent_system_prompt(_ground_plan(items), date(2026, 10, 29))

    transfers = offline_transfers(prompt)

    # Home → SFO, NRT → hotel, hotel → home.
    assert [(t["from_place"]["name"], t["to_place"]["name"]) for t in transfers] == [
        ("Home (San Francisco)", "San Francisco International Airport"),
        ("Narita International Airport", "Hotel Okura"),
        ("Hotel Okura", "Home (San Francisco)"),
    ]
    assert find_gaps([*items, *(_as_item(t) for t in transfers)], HOME) == []


def test_offline_transfers_need_the_gap_list() -> None:
    assert offline_transfers("no gaps here") == []


def test_offline_trip_sets_an_origin_only_when_the_plan_has_none() -> None:
    today = date(2026, 11, 3)

    assert "origin" not in offline_trip(today, has_origin=True)
    assert offline_trip(today, has_origin=False)["origin"]["city"] == "Philadelphia"
    assert offline_trip(today, has_origin=True)["start_date"] == "2026-12-03"
