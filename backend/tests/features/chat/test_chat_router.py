from typing import Any

from httpx import AsyncClient

from features.chat.offline import OFFLINE_AGENT_REPLY, OFFLINE_REDIRECT_REPLY
from features.chat.pipeline import EMPTY_REPLY_AFTER_TOOLS
from features.chat.schemas import (
    DoneEvent,
    ErrorEvent,
    GuardrailEvent,
    GuardrailVerdict,
    PlanUpdatedEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from shared.llm.fake import FakeLLM, json_reply, text, tool_calls
from shared.llm.types import LLMError
from shared.search.fake import FakeSearchProvider
from tests.conftest import RegisterUser
from tests.features.chat.conftest import HOTEL_ARGS, send

ON_TOPIC = json_reply(GuardrailVerdict(on_topic=True, confidence=0.95, topic="lodging"))
OFF_TOPIC = json_reply(GuardrailVerdict(on_topic=False, confidence=0.95, topic="math"))


async def _messages(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> list[dict[str, Any]]:
    response = await client.get(f"/plans/{plan_id}/messages", headers=headers)
    assert response.status_code == 200, response.text
    messages: list[dict[str, Any]] = response.json()
    return messages


# ---------------------------------------------------------------------------
# On-topic turn
# ---------------------------------------------------------------------------


async def test_on_topic_turn_runs_tools_and_stores_reply(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(ON_TOPIC, tool_calls(("add_lodging", HOTEL_ARGS)), text("Hotel Okura added!"))

    events = await send(client, headers, plan["id"], "Book me Hotel Okura May 2-6")

    assert [type(e) for e in events if not isinstance(e, TokenEvent)] == [
        GuardrailEvent,
        ToolCallEvent,
        ToolResultEvent,
        PlanUpdatedEvent,
        DoneEvent,
    ]
    guard = events[0]
    assert isinstance(guard, GuardrailEvent) and guard.on_topic and guard.topic == "lodging"
    updated = next(e for e in events if isinstance(e, PlanUpdatedEvent))
    assert updated.plan.version == plan["version"] + 1
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.message.content == "Hotel Okura added!"
    assert [(t.name, t.ok) for t in done.message.tool_calls] == [("add_lodging", True)]

    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["kind"]) for m in stored] == [
        ("user", "message"),
        ("assistant", "message"),
    ]
    assert stored[0]["content"] == "Book me Hotel Okura May 2-6"
    assert stored[1]["tool_calls"][0]["arguments"] == HOTEL_ARGS

    # The item really is on the plan.
    response = await client.get(f"/plans/{plan['id']}", headers=headers)
    assert [item["title"] for item in response.json()["items"]] == ["Hotel Okura"]


async def test_silent_reply_after_tools_gets_a_fallback(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(ON_TOPIC, tool_calls(("add_lodging", HOTEL_ARGS)), text(""))

    events = await send(client, headers, plan["id"], "Add Hotel Okura")

    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.message.content == EMPTY_REPLY_AFTER_TOOLS


async def test_history_is_passed_to_the_agent(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(ON_TOPIC, text("Okura or Park Hyatt?"), ON_TOPIC, text("Okura it is."))

    await send(client, headers, plan["id"], "Which hotels in Tokyo?")
    await send(client, headers, plan["id"], "the first one")

    agent_request = fake_llm.requests[-1]
    assert [(m.role, m.content) for m in agent_request.messages[1:]] == [
        ("user", "Which hotels in Tokyo?"),
        ("assistant", "Okura or Park Hyatt?"),
        ("user", "the first one"),
    ]
    guard_prompt = fake_llm.requests[-2].messages[-1].content
    assert "Assistant: Okura or Park Hyatt?" in guard_prompt


# ---------------------------------------------------------------------------
# Off-topic turn
# ---------------------------------------------------------------------------


async def test_off_topic_message_gets_redirect_without_tools(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(OFF_TOPIC, text("Fun question! Shall we pick a hotel in Tokyo?"))

    events = await send(client, headers, plan["id"], "What is 17 * 23?")

    guard = events[0]
    assert isinstance(guard, GuardrailEvent) and not guard.on_topic
    assert not any(isinstance(e, ToolCallEvent | PlanUpdatedEvent) for e in events)
    redirect_request = fake_llm.requests[1]
    assert redirect_request.tools == []
    assert '"Tokyo in May"' in redirect_request.messages[0].content
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.message.kind == "redirect"

    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["kind"]) for m in stored] == [
        ("user", "redirect"),
        ("assistant", "redirect"),
    ]


async def test_redirected_exchange_is_left_out_of_later_context(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(OFF_TOPIC, text("Let's talk travel!"), ON_TOPIC, text("Sure."))

    await send(client, headers, plan["id"], "Tell me a joke")
    await send(client, headers, plan["id"], "Find a hotel")

    agent_request = fake_llm.requests[-1]
    assert [m.content for m in agent_request.messages[1:]] == ["Find a hotel"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_model_failure_emits_and_stores_error(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(LLMError("Could not reach Ollama at http://localhost:11434."))

    events = await send(client, headers, plan["id"], "Plan my trip")

    (error,) = events
    assert isinstance(error, ErrorEvent)
    assert error.detail.startswith("Could not reach Ollama")
    assert error.message is not None and error.message.kind == "error"
    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["kind"]) for m in stored] == [("user", "message"), ("system", "error")]


async def test_unexpected_failure_emits_generic_error(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    # Nothing queued: the strict FakeLLM raises RuntimeError on the first call.
    events = await send(client, headers, plan["id"], "Plan my trip")

    (error,) = events
    assert isinstance(error, ErrorEvent)
    assert error.detail == "Something went wrong while answering. Please try again."


# ---------------------------------------------------------------------------
# Access + validation
# ---------------------------------------------------------------------------


async def test_other_users_plan_is_not_found(
    client: AsyncClient,
    register_user: RegisterUser,
    plan: dict[str, Any],
    fake_llm: FakeLLM,
) -> None:
    other = await register_user("eve@example.com")

    chat = await client.post(f"/plans/{plan['id']}/chat", json={"content": "hi"}, headers=other)
    history = await client.get(f"/plans/{plan['id']}/messages", headers=other)

    assert chat.status_code == 404
    assert history.status_code == 404
    assert fake_llm.requests == []


async def test_requires_auth(client: AsyncClient, plan: dict[str, Any]) -> None:
    response = await client.post(f"/plans/{plan['id']}/chat", json={"content": "hi"})

    assert response.status_code == 401


async def test_blank_or_huge_messages_are_rejected(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    for content in ["   ", "x" * 4001]:
        response = await client.post(
            f"/plans/{plan['id']}/chat", json={"content": content}, headers=headers
        )
        assert response.status_code == 422

    assert fake_llm.requests == []
    assert await _messages(client, headers, plan["id"]) == []


# ---------------------------------------------------------------------------
# Offline mode (LLM_PROVIDER=fake with the default canned replies)
# ---------------------------------------------------------------------------


async def test_offline_mode_answers_without_ollama(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    on_topic = await send(client, headers, plan["id"], "Find me a hotel")
    off_topic = await send(client, headers, plan["id"], "Explain quantum computing")

    first, second = on_topic[-1], off_topic[-1]
    assert isinstance(first, DoneEvent) and first.message.content == OFFLINE_AGENT_REPLY
    assert isinstance(second, DoneEvent) and second.message.content == OFFLINE_REDIRECT_REPLY
    assert second.message.kind == "redirect"


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


async def test_web_search_turn_streams_results_without_changing_the_plan(
    client: AsyncClient,
    headers: dict[str, str],
    plan: dict[str, Any],
    fake_llm: FakeLLM,
    fake_search: FakeSearchProvider,
) -> None:
    search = ("web_search", {"query": "ryokan", "kind": "hotels"})
    fake_llm.queue(ON_TOPIC, tool_calls(search), text("Here are some ryokan."))

    events = await send(client, headers, plan["id"], "Find me a ryokan")

    assert [type(e) for e in events if not isinstance(e, TokenEvent)] == [
        GuardrailEvent,
        ToolCallEvent,
        ToolResultEvent,
        DoneEvent,
    ]
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.ok and result.name == "web_search"
    # The model saw the (fake) results as the tool message.
    tool_message = fake_llm.requests[-1].messages[-1]
    assert tool_message.role == "tool" and "https://example.com/hotels/1" in tool_message.content


async def test_repeated_searches_across_turns_hit_the_cache(
    client: AsyncClient,
    headers: dict[str, str],
    plan: dict[str, Any],
    fake_llm: FakeLLM,
    fake_search: FakeSearchProvider,
) -> None:
    search = ("web_search", {"query": "ryokan", "kind": "hotels"})
    for _ in range(2):
        fake_llm.queue(ON_TOPIC, tool_calls(search), text("Here are some ryokan."))
        events = await send(client, headers, plan["id"], "Find me a ryokan")
        assert isinstance(events[-1], DoneEvent)

    assert [s.query for s in fake_search.searches] == ["ryokan in Tokyo May 2027"]
