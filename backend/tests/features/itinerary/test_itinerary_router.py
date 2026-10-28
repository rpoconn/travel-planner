from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from features.itinerary.models import Item
from tests.conftest import RegisterUser

PLAN: dict[str, Any] = {
    "title": "Tokyo in May",
    "start_date": "2027-05-01",
    "end_date": "2027-05-08",
}

FLIGHT: dict[str, Any] = {
    "type": "flight",
    "title": "SFO → NRT",
    "start": "2027-05-01T11:00:00",
    "end": "2027-05-02T14:30:00",
    "timezone": "America/Los_Angeles",
    "end_timezone": "Asia/Tokyo",
    "carrier": "ANA",
    "flight_number": "NH7",
    "from_place": {"name": "San Francisco International Airport", "iata": "SFO"},
    "to_place": {"name": "Narita International Airport", "iata": "NRT"},
    "direction": "outbound",
    "cost": {"amount": "1240.50", "currency": "USD"},
}

HOTEL: dict[str, Any] = {
    "type": "lodging",
    "title": "Hotel Okura",
    "start": "2027-05-02T15:00:00",
    "end": "2027-05-06T11:00:00",
    "timezone": "Asia/Tokyo",
    "place": {"name": "Hotel Okura", "city": "Tokyo"},
}

UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
async def headers(register_user: RegisterUser) -> dict[str, str]:
    return await register_user("ada@example.com")


@pytest.fixture
async def plan(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/plans", json=PLAN, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def _add(
    client: AsyncClient, headers: dict[str, str], plan_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(f"/plans/{plan_id}/items", json=body, headers=headers)
    assert response.status_code == 201, response.text
    item: dict[str, Any] = response.json()
    return item


async def _get_plan(client: AsyncClient, headers: dict[str, str], plan_id: str) -> dict[str, Any]:
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    assert response.status_code == 200, response.text
    fetched: dict[str, Any] = response.json()
    return fetched


# ---------------------------------------------------------------------------
# Create / read
# ---------------------------------------------------------------------------


async def test_new_plan_has_no_items(plan: dict[str, Any]) -> None:
    assert plan["items"] == []


async def test_add_item_returns_it_in_local_time_and_bumps_version(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    item = await _add(client, headers, plan["id"], FLIGHT)

    assert item["type"] == "flight"
    assert item["plan_id"] == plan["id"]
    assert item["created_by"] == "user"
    assert item["status"] == "proposed"
    assert item["start"] == "2027-05-01T11:00:00-07:00"
    assert item["end"] == "2027-05-02T14:30:00+09:00"
    assert item["cost"] == {"amount": "1240.50", "currency": "USD"}
    after = await _get_plan(client, headers, plan["id"])
    assert after["version"] == plan["version"] + 1
    assert [i["id"] for i in after["items"]] == [item["id"]]


async def test_items_are_listed_by_start_time(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    hotel = await _add(client, headers, plan["id"], HOTEL)
    flight = await _add(client, headers, plan["id"], FLIGHT)

    listed = await client.get(f"/plans/{plan['id']}/items", headers=headers)

    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()] == [flight["id"], hotel["id"]]
    assert [i["id"] for i in (await _get_plan(client, headers, plan["id"]))["items"]] == [
        flight["id"],
        hotel["id"],
    ]


@pytest.mark.parametrize(
    "body",
    [
        {**HOTEL, "end": "2027-05-01T11:00:00"},  # ends before it starts
        {**HOTEL, "timezone": "Nowhere/Special"},
        {**HOTEL, "type": "cruise"},
        {key: value for key, value in HOTEL.items() if key != "place"},
    ],
)
async def test_invalid_items_are_rejected(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], body: dict[str, Any]
) -> None:
    response = await client.post(f"/plans/{plan['id']}/items", json=body, headers=headers)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------


async def test_patch_merges_onto_the_item_and_bumps_version(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    hotel = await _add(client, headers, plan["id"], HOTEL)
    before = await _get_plan(client, headers, plan["id"])

    response = await client.patch(
        f"/plans/{plan['id']}/items/{hotel['id']}",
        json={"type": "lodging", "start": "2027-05-03T15:00:00", "status": "confirmed"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["start"] == "2027-05-03T15:00:00+09:00"
    assert updated["status"] == "confirmed"
    assert updated["end"] == hotel["end"]
    assert updated["place"] == hotel["place"]
    assert (await _get_plan(client, headers, plan["id"]))["version"] == before["version"] + 1


@pytest.mark.parametrize(
    ("patch", "detail"),
    [
        ({"type": "flight", "carrier": "JAL"}, "Item is a lodging, not a flight"),
        ({"type": "lodging", "title": None}, "title"),
        ({"type": "lodging", "end": "2027-05-01T10:00:00"}, "end must be at or after start"),
    ],
)
async def test_invalid_patches_are_rejected(
    client: AsyncClient,
    headers: dict[str, str],
    plan: dict[str, Any],
    patch: dict[str, Any],
    detail: str,
) -> None:
    hotel = await _add(client, headers, plan["id"], HOTEL)

    response = await client.patch(
        f"/plans/{plan['id']}/items/{hotel['id']}", json=patch, headers=headers
    )

    assert response.status_code == 422
    assert detail in response.json()["detail"]
    unchanged = await _get_plan(client, headers, plan["id"])
    assert unchanged["items"][0] == hotel


async def test_remove_item(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    hotel = await _add(client, headers, plan["id"], HOTEL)

    deleted = await client.delete(f"/plans/{plan['id']}/items/{hotel['id']}", headers=headers)
    again = await client.delete(f"/plans/{plan['id']}/items/{hotel['id']}", headers=headers)

    assert deleted.status_code == 204
    assert again.status_code == 404
    after = await _get_plan(client, headers, plan["id"])
    assert after["items"] == []
    assert after["version"] == plan["version"] + 2  # add + remove


async def test_items_cannot_be_reached_through_another_plan(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    hotel = await _add(client, headers, plan["id"], HOTEL)
    other_plan = (await client.post("/plans", json={}, headers=headers)).json()
    url = f"/plans/{other_plan['id']}/items/{hotel['id']}"

    patched = await client.patch(url, json={"type": "lodging", "title": "x"}, headers=headers)
    deleted = await client.delete(url, headers=headers)

    assert patched.status_code == 404
    assert patched.json()["detail"] == "Item not found"
    assert deleted.status_code == 404


async def test_deleting_a_plan_deletes_its_items(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    await _add(client, headers, plan["id"], HOTEL)

    await client.delete(f"/plans/{plan['id']}", headers=headers)

    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session:
        remaining = await session.scalar(select(func.count()).select_from(Item))
    assert remaining == 0


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


async def test_timeline_endpoint(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    flight = await _add(client, headers, plan["id"], FLIGHT)
    hotel = await _add(client, headers, plan["id"], HOTEL)

    response = await client.get(f"/plans/{plan['id']}/timeline", headers=headers)

    assert response.status_code == 200, response.text
    timeline = response.json()
    assert timeline["version"] == plan["version"] + 2
    assert [day["date"] for day in timeline["days"]][:2] == ["2027-05-01", "2027-05-02"]
    assert len(timeline["days"]) == 8
    assert [e["item"]["id"] for e in timeline["days"][1]["entries"]] == [flight["id"]]
    assert [
        (s["item"]["id"], s["start_day_index"], s["end_day_index"]) for s in timeline["lodging"]
    ] == [(hotel["id"], 1, 5)]
    assert {"no_lodging", "missing_transfer"} <= {w["kind"] for w in timeline["warnings"]}


# ---------------------------------------------------------------------------
# Auth / isolation
# ---------------------------------------------------------------------------


async def test_itinerary_requires_auth(client: AsyncClient, plan: dict[str, Any]) -> None:
    response = await client.get(f"/plans/{plan['id']}/items")

    assert response.status_code == 401


async def test_users_cannot_touch_each_others_itineraries(
    client: AsyncClient,
    headers: dict[str, str],
    plan: dict[str, Any],
    register_user: RegisterUser,
) -> None:
    hotel = await _add(client, headers, plan["id"], HOTEL)
    other = await register_user("bob@example.com")
    base = f"/plans/{plan['id']}"
    item_url = f"{base}/items/{hotel['id']}"

    responses = [
        await client.get(f"{base}/items", headers=other),
        await client.post(f"{base}/items", json=HOTEL, headers=other),
        await client.patch(item_url, json={"type": "lodging", "title": "Mine"}, headers=other),
        await client.delete(item_url, headers=other),
        await client.get(f"{base}/timeline", headers=other),
        await client.get(f"/plans/{UNKNOWN_ID}/items", headers=headers),
    ]

    assert [r.status_code for r in responses] == [404] * 6
    assert (await _get_plan(client, headers, plan["id"]))["items"] == [hotel]
