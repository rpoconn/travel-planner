"""Going back to a stage a later change broke, over HTTP: at the ground stage, removing the
only stay through the items API leaves nights uncovered, so the next turn returns the plan
to lodging with a divider saying why, and asks that stage's question."""

from fastapi import FastAPI
from httpx import AsyncClient

from features.planning.schemas import StageRecord
from features.plans.schemas import PlanStage
from tests.features.planning.planning_http import (
    BASICS,
    asking,
    create_plan,
    done,
    messages,
    post,
    reach,
    stored,
)


async def _at_ground(app: FastAPI, client: AsyncClient, headers: dict[str, str]) -> tuple[str, str]:
    """A Lisbon trip without flights, every night at a friend's flat and a dinner there, at
    the ground stage. Returns the plan and the stay."""
    plan_id = await create_plan(client, headers, **BASICS)
    await reach(app, client, headers, plan_id, PlanStage.GROUND)
    plan = (await client.get(f"/plans/{plan_id}", headers=headers)).json()
    stay: str = next(item["id"] for item in plan["items"] if item["type"] == "lodging")
    return plan_id, stay


async def test_removing_the_only_stay_returns_the_plan_to_lodging(
    app: FastAPI, client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id, stay = await _at_ground(app, client, headers)
    response = await client.delete(f"/plans/{plan_id}/items/{stay}", headers=headers)
    assert response.status_code == 204, response.text

    # The plan as read now says to go back; the next turn does it.
    plan = (await client.get(f"/plans/{plan_id}", headers=headers)).json()
    assert plan["assessment"]["next_action"] == {
        "type": "return_to_stage",
        "to": "lodging",
        "reason": "a place to stay the night of May 1",
    }

    events = await post(client, headers, plan_id, "continue")

    assert stored(events) == [
        ("stage", "Back to lodging: the trip still needs a place to stay the night of May 1."),
        ("expected_input", "What kind of place do you want to stay in?"),
    ]
    assert done(events).plan.stage == PlanStage.LODGING
    assert asking(events) == "lodging_preferences"
    divider = (await messages(client, headers, plan_id))[0]
    assert divider.payload == StageRecord(from_stage=PlanStage.GROUND, to_stage=PlanStage.LODGING)


async def test_an_untouched_later_stage_carries_on(
    app: FastAPI, client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id, _stay = await _at_ground(app, client, headers)

    events = await post(client, headers, plan_id, "continue")

    # Nothing earlier is broken, so the ground stage does its own work (a transfer search).
    assert done(events).plan.stage == PlanStage.GROUND
    assert all(kind != "stage" for kind, _ in stored(events))
