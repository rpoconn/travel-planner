from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient

from features.chat.offline import (
    OFFLINE_ADDED_REPLY,
    OFFLINE_AGENT_REPLY,
    OFFLINE_FIX_REPLY,
    OFFLINE_PROPOSED_REPLY,
    OFFLINE_REDIRECT_REPLY,
)
from features.chat.pipeline import (
    APPROVED_TEXT,
    DENIED_REPLY,
    DENIED_TEXT,
    EMPTY_REPLY_AFTER_PROPOSAL,
)
from features.chat.prompts import APPROVAL_MARKER, FIX_REQUEST_MARKER, REVISE_MARKER
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
from shared.rate_limit import CHAT_BUCKET, RateLimiter
from shared.search.fake import FakeSearchProvider
from tests.conftest import RegisterUser
from tests.features.chat.conftest import HOTEL_ARGS, decide, done_message, send

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


async def test_on_topic_turn_proposes_changes_and_stores_reply(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(
        ON_TOPIC, tool_calls(("add_lodging", HOTEL_ARGS)), text("I've proposed Hotel Okura.")
    )

    events = await send(client, headers, plan["id"], "Hotel Okura May 2-6 please")

    # No plan_updated: nothing is applied until the user approves.
    assert [type(e) for e in events if not isinstance(e, TokenEvent)] == [
        GuardrailEvent,
        ToolCallEvent,
        ToolResultEvent,
        DoneEvent,
    ]
    guard = events[0]
    assert isinstance(guard, GuardrailEvent) and guard.on_topic and guard.topic == "lodging"
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.ok and result.proposed
    done = done_message(events)
    assert done.message.content == "I've proposed Hotel Okura."
    assert [(t.name, t.ok, t.proposed) for t in done.message.tool_calls] == [
        ("add_lodging", True, True)
    ]
    proposal = done.message.proposal
    assert proposal is not None and proposal.status == "pending"
    assert proposal.message_id == done.message.id
    assert proposal.plan_version == plan["version"]
    (change,) = proposal.changes
    assert change.name == "add_lodging" and change.call_id == result.call_id
    assert change.summary == "Add lodging “Hotel Okura” on 2027-05-02 15:00"

    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["kind"]) for m in stored] == [
        ("user", "message"),
        ("assistant", "message"),
    ]
    assert stored[0]["proposal"] is None
    assert stored[1]["proposal"]["id"] == str(proposal.id)
    assert stored[1]["tool_calls"][0]["arguments"] == HOTEL_ARGS

    # The item is not on the plan yet.
    response = await client.get(f"/plans/{plan['id']}", headers=headers)
    assert response.json()["items"] == []
    assert response.json()["version"] == plan["version"]


async def test_silent_reply_after_a_proposal_gets_a_fallback(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(ON_TOPIC, tool_calls(("add_lodging", HOTEL_ARGS)), text(""))

    events = await send(client, headers, plan["id"], "Add Hotel Okura")

    assert done_message(events).message.content == EMPTY_REPLY_AFTER_PROPOSAL


async def test_a_new_proposal_supersedes_the_pending_one(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    first = await _propose_hotel(client, headers, plan["id"], fake_llm)
    second = await _propose_hotel(client, headers, plan["id"], fake_llm, title="Park Hyatt")

    stored = await _messages(client, headers, plan["id"])
    statuses = {m["proposal"]["id"]: m["proposal"]["status"] for m in stored if m["proposal"]}
    assert statuses == {first: "superseded", second: "pending"}
    # The old card can no longer be decided.
    response = await client.post(
        f"/plans/{plan['id']}/chat/proposals/{first}/decision",
        json={"action": "approve"},
        headers=headers,
    )
    assert response.status_code == 409


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


async def test_chat_is_rate_limited_per_user(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    plan: dict[str, Any],
    register_user: RegisterUser,
) -> None:
    app.state.rate_limiters[CHAT_BUCKET] = RateLimiter(1)
    await send(client, headers, plan["id"], "hi")

    limited = await client.post(
        f"/plans/{plan['id']}/chat", json={"content": "hi again"}, headers=headers
    )

    assert limited.status_code == 429
    assert limited.json()["detail"].startswith("You're sending messages too quickly.")
    assert "retry-after" in limited.headers
    # Nothing was stored for the rejected message.
    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["content"]) for m in stored][:1] == [("user", "hi")]
    assert len(stored) == 2

    # Another user has their own budget.
    other = await register_user("grace@example.com")
    created = await client.post("/plans", json={"title": "Other"}, headers=other)
    await send(client, other, created.json()["id"], "hi")


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


async def test_offline_mode_add_command_proposes_then_approve_applies(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    events = await send(client, headers, plan["id"], "Add a visit to the Louvre!")

    call = next(e for e in events if isinstance(e, ToolCallEvent))
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert call.name == "add_activity"
    assert result.ok and result.proposed, result.summary
    assert not any(isinstance(e, PlanUpdatedEvent) for e in events)
    done = done_message(events)
    assert done.message.content == OFFLINE_PROPOSED_REPLY
    assert done.message.proposal is not None

    approved = await decide(client, headers, plan["id"], str(done.message.proposal.id), "approve")

    updated = next(e for e in approved if isinstance(e, PlanUpdatedEvent))
    assert [item.title for item in updated.plan.items] == ["Visit to the Louvre"]
    follow_up = done_message(approved)
    assert follow_up.message.content == OFFLINE_ADDED_REPLY
    assert [t.summary for t in follow_up.message.tool_calls] == [
        "Added activity “Visit to the Louvre”"
    ]


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


# ---------------------------------------------------------------------------
# "Fix it" (a timeline warning handed to the assistant)
# ---------------------------------------------------------------------------

NO_LODGING: dict[str, Any] = {
    "kind": "no_lodging",
    "message": "No lodging for the night of May 1",
    "date": "2027-05-01",
    "item_ids": [],
}


async def _add_hotel(client: AsyncClient, headers: dict[str, str], plan_id: str) -> str:
    body = {"type": "lodging", **HOTEL_ARGS}
    response = await client.post(f"/plans/{plan_id}/items", json=body, headers=headers)
    assert response.status_code == 201, response.text
    item_id: str = response.json()["id"]
    return item_id


async def test_fix_turn_offers_all_tools_and_skips_the_topic_check(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    hotel_id = await _add_hotel(client, headers, plan["id"])
    warning = {
        "kind": "outside_trip",
        "message": "Hotel Okura is outside the trip dates",
        "date": "2027-05-02",
        "item_ids": [hotel_id],
    }
    # No guardrail verdict queued: a fix turn skips the topic check.
    fake_llm.queue(text("Should Hotel Okura move to May 1-5, or should the trip get longer?"))

    events = await send(client, headers, plan["id"], f"Fix: {warning['message']}", fix=warning)

    assert events[0] == GuardrailEvent(on_topic=True, topic="itinerary fix")
    (request,) = fake_llm.requests
    assert request.format is None
    assert "update_item" in [spec.name for spec in request.tools]
    latest = request.messages[-1].content
    assert latest.startswith("Fix: Hotel Okura is outside the trip dates\n")
    assert FIX_REQUEST_MARKER in latest
    assert "kind: outside_trip" in latest and "date: 2027-05-02" in latest
    assert f"{hotel_id}: Hotel Okura (lodging, 2027-05-02 15:00)" in latest

    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["content"]) for m in stored] == [
        ("user", "Fix: Hotel Okura is outside the trip dates"),
        ("assistant", "Should Hotel Okura move to May 1-5, or should the trip get longer?"),
    ]


async def test_a_fix_is_proposed_and_applied_on_approval(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(tool_calls(("add_lodging", HOTEL_ARGS)), text("I've proposed Hotel Okura."))
    events = await send(
        client, headers, plan["id"], "Fix: No lodging for the night of May 1", NO_LODGING
    )
    proposal = done_message(events).message.proposal
    assert proposal is not None

    fake_llm.queue(text("Hotel Okura is booked in your plan. Next: flights?"))
    approved = await decide(client, headers, plan["id"], str(proposal.id), "approve")

    assert any(isinstance(e, PlanUpdatedEvent) for e in approved)
    # Only the visible fix text is in the history, not the fix instructions.
    agent_request = fake_llm.requests[-1]
    assert agent_request.messages[1].content == "Fix: No lodging for the night of May 1"
    assert FIX_REQUEST_MARKER not in agent_request.messages[2].content


async def test_fix_with_a_stale_item_id_still_answers(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    gone = str(uuid4())
    warning = {"kind": "overlap", "message": "A overlaps B", "item_ids": [gone]}
    fake_llm.queue(text("That item is gone, so there's nothing to fix."))

    events = await send(client, headers, plan["id"], "Fix: A overlaps B", fix=warning)

    assert isinstance(events[-1], DoneEvent)
    assert f"{gone}: no longer in the plan" in fake_llm.requests[0].messages[-1].content


async def test_invalid_fix_is_rejected(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    for fix in [{**NO_LODGING, "kind": "bogus"}, {**NO_LODGING, "item_ids": ["x"]}]:
        response = await client.post(
            f"/plans/{plan['id']}/chat", json={"content": "Fix: it", "fix": fix}, headers=headers
        )
        assert response.status_code == 422

    assert fake_llm.requests == []
    assert await _messages(client, headers, plan["id"]) == []


async def test_fix_model_failure_emits_and_stores_error(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    fake_llm.queue(LLMError("Could not reach Ollama at http://localhost:11434."))

    events = await send(client, headers, plan["id"], "Fix: No lodging", fix=NO_LODGING)

    assert isinstance(events[0], GuardrailEvent)
    error = events[-1]
    assert isinstance(error, ErrorEvent) and error.detail.startswith("Could not reach Ollama")
    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["kind"]) for m in stored] == [("user", "message"), ("system", "error")]


async def test_offline_mode_fix_proposes_without_tools(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    events = await send(client, headers, plan["id"], "Fix: No lodging", fix=NO_LODGING)

    assert not any(isinstance(e, ToolCallEvent) for e in events)
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.message.content == OFFLINE_FIX_REPLY


# ---------------------------------------------------------------------------
# Proposal decisions (approve / deny / revise a proposal card)
# ---------------------------------------------------------------------------


async def _propose_hotel(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    title: str = "Hotel Okura",
) -> str:
    """Run a turn in which the assistant proposes a hotel; returns the proposal id."""
    fake_llm.queue(
        ON_TOPIC,
        tool_calls(("add_lodging", {**HOTEL_ARGS, "title": title})),
        text(f"I've proposed {title}."),
    )
    proposal = done_message(
        await send(client, headers, plan_id, f"{title} please")
    ).message.proposal
    assert proposal is not None
    return str(proposal.id)


def _proposal_statuses(stored: list[dict[str, Any]]) -> list[str]:
    return [m["proposal"]["status"] for m in stored if m["proposal"] is not None]


async def test_approve_applies_the_changes_and_lets_the_agent_confirm(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    proposal_id = await _propose_hotel(client, headers, plan["id"], fake_llm)
    fake_llm.queue(text("Hotel Okura is on your plan. Shall we look at flights next?"))

    events = await decide(client, headers, plan["id"], proposal_id, "approve")

    # No topic check for a decision; the writes stream like tool calls.
    assert [type(e) for e in events if not isinstance(e, TokenEvent)] == [
        GuardrailEvent,
        ToolCallEvent,
        ToolResultEvent,
        PlanUpdatedEvent,
        DoneEvent,
    ]
    assert events[0] == GuardrailEvent(on_topic=True, topic="proposal decision")
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.ok and not result.proposed
    assert result.summary == "Added lodging “Hotel Okura”"
    updated = next(e for e in events if isinstance(e, PlanUpdatedEvent))
    assert [item.title for item in updated.plan.items] == ["Hotel Okura"]
    done = done_message(events)
    assert done.message.content == "Hotel Okura is on your plan. Shall we look at flights next?"
    assert [t.summary for t in done.message.tool_calls] == ["Added lodging “Hotel Okura”"]
    assert done.message.proposal is None

    # The follow-up turn is told what was applied (with the new id), and its history shows
    # the proposal as approved.
    request = fake_llm.requests[-1]
    assert request.tools  # a normal agent turn: it may propose the next step
    latest = request.messages[-1].content
    assert latest.startswith(APPROVAL_MARKER)
    item_id = updated.plan.items[0].id
    assert f'Added lodging "Hotel Okura" with id {item_id}' in latest
    proposing = request.messages[-2].content
    assert proposing.startswith("I've proposed Hotel Okura.")
    assert "[Proposed changes (approved):\n- Add lodging “Hotel Okura”" in proposing

    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["kind"], m["content"]) for m in stored[2:]] == [
        ("user", "decision", APPROVED_TEXT),
        ("assistant", "message", done.message.content),
    ]
    assert _proposal_statuses(stored) == ["approved"]
    assert stored[1]["proposal"]["decided_at"] is not None


async def test_approve_applies_nothing_if_a_change_no_longer_fits(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    hotel_id = await _add_hotel(client, headers, plan["id"])
    second: dict[str, Any] = {**HOTEL_ARGS, "title": "Ryokan", "start": "2027-05-06T15:00:00"}
    second["end"] = "2027-05-08T10:00:00"
    fake_llm.queue(
        ON_TOPIC,
        tool_calls(
            ("add_lodging", second),
            ("update_item", {"item_id": hotel_id, "changes": {"notes": "Late check-in"}}),
        ),
        text("Proposed both."),
    )
    events = await send(client, headers, plan["id"], "Add a ryokan and note late check-in")
    proposal = done_message(events).message.proposal
    assert proposal is not None and len(proposal.changes) == 2
    # The hotel disappears (another tab) before the user approves.
    await client.delete(f"/plans/{plan['id']}/items/{hotel_id}", headers=headers)
    version = (await client.get(f"/plans/{plan['id']}", headers=headers)).json()["version"]
    fake_llm.queue(text("The hotel is gone, so I couldn't apply that."))

    approved = await decide(client, headers, plan["id"], str(proposal.id), "approve")

    assert not any(isinstance(e, ToolCallEvent | PlanUpdatedEvent) for e in approved)
    latest = fake_llm.requests[-1].messages[-1].content
    assert latest.startswith(APPROVAL_MARKER) and "Nothing was applied." in latest
    assert "No item with that id" in latest
    response = await client.get(f"/plans/{plan['id']}", headers=headers)
    assert response.json()["items"] == [] and response.json()["version"] == version
    assert _proposal_statuses(await _messages(client, headers, plan["id"])) == ["failed"]


async def test_deny_changes_nothing_and_needs_no_model(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    proposal_id = await _propose_hotel(client, headers, plan["id"], fake_llm)
    requests = len(fake_llm.requests)

    events = await decide(client, headers, plan["id"], proposal_id, "deny")

    assert len(fake_llm.requests) == requests
    assert done_message(events).message.content == DENIED_REPLY
    stored = await _messages(client, headers, plan["id"])
    assert [(m["role"], m["kind"], m["content"]) for m in stored[2:]] == [
        ("user", "decision", DENIED_TEXT),
        ("assistant", "message", DENIED_REPLY),
    ]
    assert _proposal_statuses(stored) == ["denied"]
    response = await client.get(f"/plans/{plan['id']}", headers=headers)
    assert response.json()["items"] == []


async def test_revise_hands_the_text_to_the_agent_which_proposes_again(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any], fake_llm: FakeLLM
) -> None:
    proposal_id = await _propose_hotel(client, headers, plan["id"], fake_llm)
    ryokan = {**HOTEL_ARGS, "title": "Ryokan Sawanoya"}
    # No guardrail verdict queued: a revision skips the topic check.
    fake_llm.queue(tool_calls(("add_lodging", ryokan)), text("I've proposed a ryokan instead."))

    events = await decide(
        client, headers, plan["id"], proposal_id, "revise", "A ryokan instead, please"
    )

    (call,) = [e for e in events if isinstance(e, ToolCallEvent)]
    assert call.arguments["title"] == "Ryokan Sawanoya"
    agent_request = fake_llm.requests[-2]
    assert agent_request.streamed and agent_request.tools
    latest = agent_request.messages[-1].content
    assert latest.startswith(REVISE_MARKER)
    assert "A ryokan instead, please" in latest
    assert "- Add lodging “Hotel Okura” on 2027-05-02 15:00" in latest
    new_proposal = done_message(events).message.proposal
    assert new_proposal is not None and new_proposal.status == "pending"

    stored = await _messages(client, headers, plan["id"])
    assert (stored[2]["role"], stored[2]["kind"], stored[2]["content"]) == (
        "user",
        "decision",
        "A ryokan instead, please",
    )
    assert _proposal_statuses(stored) == ["revised", "pending"]


async def test_decisions_are_validated_before_the_stream(
    client: AsyncClient,
    headers: dict[str, str],
    plan: dict[str, Any],
    fake_llm: FakeLLM,
    register_user: RegisterUser,
) -> None:
    proposal_id = await _propose_hotel(client, headers, plan["id"], fake_llm)
    url = f"/plans/{plan['id']}/chat/proposals/{proposal_id}/decision"

    for body in [{"action": "revise"}, {"action": "approve", "message": "hi"}, {"action": "x"}]:
        response = await client.post(url, json=body, headers=headers)
        assert response.status_code == 422, body

    missing = await client.post(
        f"/plans/{plan['id']}/chat/proposals/{uuid4()}/decision",
        json={"action": "deny"},
        headers=headers,
    )
    assert missing.status_code == 404
    other = await register_user("eve@example.com")
    stolen = await client.post(url, json={"action": "deny"}, headers=other)
    assert stolen.status_code == 404

    await decide(client, headers, plan["id"], proposal_id, "deny")
    again = await client.post(url, json={"action": "approve"}, headers=headers)
    assert again.status_code == 409
    assert again.json()["detail"] == "This proposal was already denied"
