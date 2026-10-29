from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import RegisterUser

TOKYO: dict[str, Any] = {
    "title": "Tokyo in May",
    "origin": {"name": "San Francisco International Airport", "iata": "SFO"},
    "destination": {"name": "Tokyo", "country": "Japan"},
    "start_date": "2027-05-01",
    "end_date": "2027-05-08",
    "travelers": {"adults": 2, "children": 0},
    "budget": {"amount": "4500.00", "currency": "USD"},
}


@pytest.fixture
async def headers(register_user: RegisterUser) -> dict[str, str]:
    return await register_user("ada@example.com")


async def _create(client: AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = await client.post("/plans", json=body, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# ---------------------------------------------------------------------------
# Create / read
# ---------------------------------------------------------------------------


async def test_create_plan_with_defaults(client: AsyncClient, headers: dict[str, str]) -> None:
    plan = await _create(client, headers)

    assert plan["title"] == "Untitled trip"
    assert plan["status"] == "draft"
    # Unknown until the user says, so the assistant asks instead of assuming one adult.
    assert plan["travelers"] is None
    assert plan["version"] == 1
    assert plan["origin"] is None


async def test_create_and_get_full_plan(client: AsyncClient, headers: dict[str, str]) -> None:
    created = await _create(client, headers, **TOKYO)

    response = await client.get(f"/plans/{created['id']}", headers=headers)

    assert response.status_code == 200
    plan = response.json()
    assert plan["title"] == "Tokyo in May"
    assert plan["origin"]["iata"] == "SFO"
    assert plan["start_date"] == "2027-05-01"
    assert plan["budget"] == {"amount": "4500.00", "currency": "USD"}
    assert plan["created_at"].endswith("Z") or "+00:00" in plan["created_at"]


async def test_create_rejects_end_before_start(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post(
        "/plans", json={"start_date": "2027-05-08", "end_date": "2027-05-01"}, headers=headers
    )

    assert response.status_code == 422


async def test_list_plans_most_recently_updated_first(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    first = await _create(client, headers, title="First")
    await _create(client, headers, title="Second")
    await client.patch(f"/plans/{first['id']}", json={"notes": "touched"}, headers=headers)

    response = await client.get("/plans", headers=headers)

    assert response.status_code == 200
    assert [p["title"] for p in response.json()] == ["First", "Second"]


async def test_get_unknown_plan_is_404(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await client.get("/plans/00000000-0000-0000-0000-000000000000", headers=headers)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------


async def test_patch_applies_only_given_fields_and_bumps_version(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(client, headers, **TOKYO)

    response = await client.patch(
        f"/plans/{created['id']}", json={"title": "Tokyo & Kyoto"}, headers=headers
    )

    assert response.status_code == 200
    plan = response.json()
    assert plan["title"] == "Tokyo & Kyoto"
    assert plan["destination"] == created["destination"]
    assert plan["version"] == 2
    assert plan["updated_at"] >= created["updated_at"]


async def test_patch_can_clear_optional_fields(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(client, headers, **TOKYO)

    response = await client.patch(
        f"/plans/{created['id']}", json={"budget": None, "end_date": None}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["budget"] is None
    assert response.json()["end_date"] is None


async def test_patch_can_clear_travelers(client: AsyncClient, headers: dict[str, str]) -> None:
    created = await _create(client, headers, **TOKYO)

    response = await client.patch(
        f"/plans/{created['id']}", json={"travelers": None}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["travelers"] is None


async def test_patch_rejects_nulling_required_fields(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(client, headers)

    response = await client.patch(f"/plans/{created['id']}", json={"title": None}, headers=headers)

    assert response.status_code == 422


async def test_patch_checks_dates_against_existing_plan(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(client, headers, **TOKYO)

    response = await client.patch(
        f"/plans/{created['id']}", json={"end_date": "2027-04-01"}, headers=headers
    )

    assert response.status_code == 422
    assert "end_date" in response.json()["detail"]


async def test_delete_plan(client: AsyncClient, headers: dict[str, str]) -> None:
    created = await _create(client, headers)

    deleted = await client.delete(f"/plans/{created['id']}", headers=headers)
    fetched = await client.get(f"/plans/{created['id']}", headers=headers)

    assert deleted.status_code == 204
    assert fetched.status_code == 404


# ---------------------------------------------------------------------------
# Auth / isolation
# ---------------------------------------------------------------------------


async def test_plans_require_auth(client: AsyncClient) -> None:
    response = await client.get("/plans")

    assert response.status_code == 401


async def test_users_cannot_see_or_modify_each_others_plans(
    client: AsyncClient, headers: dict[str, str], register_user: RegisterUser
) -> None:
    plan = await _create(client, headers, **TOKYO)
    other = await register_user("bob@example.com")
    url = f"/plans/{plan['id']}"

    listed = await client.get("/plans", headers=other)
    fetched = await client.get(url, headers=other)
    patched = await client.patch(url, json={"title": "Mine now"}, headers=other)
    deleted = await client.delete(url, headers=other)

    assert listed.json() == []
    # 404 rather than 403 so plan ids can't be probed.
    assert fetched.status_code == 404
    assert patched.status_code == 404
    assert deleted.status_code == 404
    still_there = await client.get(url, headers=headers)
    assert still_there.json()["title"] == "Tokyo in May"
