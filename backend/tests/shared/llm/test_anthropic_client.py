"""AnthropicClient against a mocked Claude Messages API (httpx2.MockTransport)."""

import json
from collections.abc import Callable

import anthropic
import httpx2
import pytest
from pydantic import JsonValue

from shared.llm.anthropic_client import AnthropicClient, strict_schema
from shared.llm.types import LLMChunk, LLMError, LLMMessage, LLMToolCall, ToolSpec
from tests.json_path import at, json_object, obj, text

Handler = Callable[[httpx2.Request], httpx2.Response]

MESSAGES = [
    LLMMessage(role="system", content="You plan trips."),
    LLMMessage(role="user", content="Plan a trip to Tokyo"),
]
TOOL = ToolSpec(name="add_lodging", description="Add a stay", parameters={"type": "object"})
# Tells the SDK to retry almost immediately instead of backing off for half a second.
FAST_RETRY = {"retry-after-ms": "1"}


def _client(
    handler: Handler, *, max_attempts: int = 3, refusal_fallback: bool = True
) -> AnthropicClient:
    sdk = anthropic.AsyncAnthropic(
        api_key="test-key",
        base_url="http://anthropic.test",
        max_retries=max_attempts - 1,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicClient(
        api_key="test-key",
        model="claude-test",
        refusal_fallback=refusal_fallback,
        client=sdk,
    )


def _message(
    content: list[JsonValue] | None = None, stop_reason: str = "end_turn"
) -> dict[str, JsonValue]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": content if content is not None else [{"type": "text", "text": "Hi"}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _error(status: int, kind: str, message: str) -> httpx2.Response:
    body = {"type": "error", "error": {"type": kind, "message": message}}
    return httpx2.Response(status, json=body, headers=FAST_RETRY)


def _sse(*events: dict[str, JsonValue]) -> httpx2.Response:
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx2.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )


def _stream_events(*texts: str, stop_reason: str = "end_turn") -> list[dict[str, JsonValue]]:
    start = _message(content=[], stop_reason="end_turn")
    start["stop_reason"] = None
    events: list[dict[str, JsonValue]] = [
        {"type": "message_start", "message": start},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    ]
    events += [
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}
        for t in texts
    ]
    events += [
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    return events


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


async def test_chat_hoists_system_prompt_and_parses_text() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_message())

    reply = await _client(handler).chat(MESSAGES)

    assert reply == LLMMessage(role="assistant", content="Hi")
    body = json_object(seen[0].content)
    assert body["model"] == "claude-test"
    assert body["system"] == "You plan trips."
    assert body["messages"] == [{"role": "user", "content": "Plan a trip to Tokyo"}]
    assert body["max_tokens"] == 16000
    # No `thinking` param: it stays adaptive, and effort sets how much of it happens.
    assert "thinking" not in body
    assert at(body, "output_config", "effort") == "low"


async def test_chat_turns_effort_up_when_thinking_is_requested() -> None:
    seen: list[dict[str, JsonValue]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json_object(request.content))
        return httpx2.Response(200, json=_message())

    client = _client(handler)
    await client.chat(MESSAGES, think=True)
    await client.chat(MESSAGES)

    assert [at(body, "output_config", "effort") for body in seen] == ["high", "low"]


async def test_chat_sends_format_as_strict_structured_output() -> None:
    seen: list[dict[str, JsonValue]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json_object(request.content))
        return httpx2.Response(200, json=_message([{"type": "text", "text": '{"city": "Tokyo"}'}]))

    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    reply = await _client(handler).chat(MESSAGES, format=schema)

    assert reply.content == '{"city": "Tokyo"}'
    assert obj(seen[0], "output_config", "format") == {
        "type": "json_schema",
        "schema": {**schema, "additionalProperties": False},
    }


async def test_refusal_fallback_is_sent_only_when_enabled() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_message())

    await _client(handler).chat(MESSAGES)
    await _client(handler, refusal_fallback=False).chat(MESSAGES)

    on, off = seen
    assert json_object(on.content)["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in on.headers.get("anthropic-beta", "")
    assert "fallbacks" not in json_object(off.content)
    assert "server-side-fallback" not in off.headers.get("anthropic-beta", "")


async def test_tools_and_tool_results_round_trip() -> None:
    seen: list[dict[str, JsonValue]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json_object(request.content))
        call: JsonValue = {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "add_lodging",
            "input": {"title": "Hotel Okura"},
        }
        return httpx2.Response(200, json=_message([{"type": "text", "text": "On it."}, call]))

    history = [
        *MESSAGES,
        LLMMessage(
            role="assistant",
            tool_calls=[LLMToolCall(name="add_lodging", arguments={"title": "Park Hyatt"})],
        ),
        LLMMessage(role="tool", tool_name="add_lodging", content="added"),
    ]
    reply = await _client(handler).chat(history, tools=[TOOL])

    assert reply.content == "On it."
    assert reply.tool_calls == [LLMToolCall(name="add_lodging", arguments={"title": "Hotel Okura"})]
    body = seen[0]
    assert text(body, "tools", 0, "name") == "add_lodging"
    assert obj(body, "tools", 0, "input_schema") == {"type": "object"}
    call_id = text(body, "messages", 1, "content", 0, "id")
    assert at(body, "messages", 1, "content", 0, "input") == {"title": "Park Hyatt"}
    assert at(body, "messages", 2, "role") == "user"
    assert text(body, "messages", 2, "content", 0, "tool_use_id") == call_id


def test_strict_schema_closes_nested_objects_but_not_field_names() -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            # A field named "type" must not be mistaken for a schema keyword.
            "type": {"type": "string"},
            "stay": {"type": "object", "properties": {"nights": {"type": "integer"}}},
            "stops": {"type": "array", "items": {"type": "object", "properties": {}}},
        },
    }

    strict = strict_schema(schema)

    assert strict["additionalProperties"] is False
    assert at(strict, "properties", "type") == {"type": "string"}
    assert at(strict, "properties", "stay", "additionalProperties") is False
    assert at(strict, "properties", "stops", "items", "additionalProperties") is False


# ---------------------------------------------------------------------------
# Errors and retries
# ---------------------------------------------------------------------------


async def test_rate_limit_is_retried_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _error(429, "rate_limit_error", "Slow down")
        return httpx2.Response(200, json=_message())

    reply = await _client(handler).chat(MESSAGES)

    assert reply.content == "Hi"
    assert calls == 2


async def test_overload_after_max_attempts_raises_llm_error() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return _error(529, "overloaded_error", "Overloaded")

    with pytest.raises(LLMError, match="having trouble"):
        await _client(handler, max_attempts=2).chat(MESSAGES)
    assert calls == 2


@pytest.mark.parametrize(
    ("status", "kind", "expected"),
    [
        (401, "authentication_error", "rejected the API key"),
        (404, "not_found_error", "Model 'claude-test' is not available"),
    ],
)
async def test_client_errors_are_not_retried_and_explain_themselves(
    status: int, kind: str, expected: str
) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return _error(status, kind, "nope")

    with pytest.raises(LLMError, match=expected):
        await _client(handler).chat(MESSAGES)
    assert calls == 1


async def test_refusal_raises_llm_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_message([], stop_reason="refusal"))

    with pytest.raises(LLMError, match="declined"):
        await _client(handler).chat(MESSAGES)


async def test_missing_api_key_is_reported_on_first_use() -> None:
    client = AnthropicClient(api_key=None, model="claude-test")

    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
        await client.chat(MESSAGES)
    await client.aclose()


# ---------------------------------------------------------------------------
# stream
# ---------------------------------------------------------------------------


async def test_stream_yields_text_deltas() -> None:
    seen: list[dict[str, JsonValue]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json_object(request.content))
        return _sse(*_stream_events("Tokyo ", "in spring."))

    chunks = [chunk async for chunk in _client(handler).stream(MESSAGES)]

    assert chunks == [LLMChunk(content="Tokyo "), LLMChunk(content="in spring.")]
    assert seen[0]["stream"] is True
    assert seen[0]["system"] == "You plan trips."


async def test_stream_retries_before_the_first_token() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _error(529, "overloaded_error", "Overloaded")
        return _sse(*_stream_events("Hello"))

    chunks = [chunk async for chunk in _client(handler).stream(MESSAGES)]

    assert chunks == [LLMChunk(content="Hello")]
    assert calls == 2


async def test_stream_error_mid_response_raises_llm_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        events = _stream_events("Partial")[:3]
        events.append(
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
        )
        return _sse(*events)

    received: list[LLMChunk] = []
    with pytest.raises(LLMError):
        async for chunk in _client(handler).stream(MESSAGES):
            received.append(chunk)
    assert received == [LLMChunk(content="Partial")]
