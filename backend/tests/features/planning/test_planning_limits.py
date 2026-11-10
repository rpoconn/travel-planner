"""Every planning POST runs the planner, so every one of them counts against the user's
rate limit (here: one a minute)."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import JsonValue

from shared.config import Settings
from shared.llm.fake import FakeLLM, text
from tests.features.planning.planning_http import LISBON, post


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"chat_rate_limit_per_minute": 1})


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("continue", None),
        ("chat", {"content": "Lisbon"}),
        ("inputs", {"type": "answer", "slot": "destination", "value": {"destination": LISBON}}),
        ("removals", {"item_id": str(uuid4())}),
        (f"proposals/{uuid4()}/decision", {"decision": "approve"}),
    ],
)
async def test_every_planning_post_is_rate_limited(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    path: str,
    body: dict[str, JsonValue] | None,
) -> None:
    fake_llm.queue(text("Hello."))
    await post(client, headers, plan_id, "continue")  # the minute's one turn

    response = await client.post(f"/plans/{plan_id}/planning/{path}", json=body, headers=headers)

    assert response.status_code == 429
    assert len(fake_llm.requests) == 1
