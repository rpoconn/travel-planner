"""OllamaClient against a mocked Ollama HTTP API (httpx.MockTransport)."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import ollama
import pytest

from shared.llm.ollama_client import OllamaClient
from shared.llm.types import LLMError, LLMMessage, ToolSpec

Handler = Callable[[httpx.Request], httpx.Response]

MESSAGES = [LLMMessage(role="user", content="Plan a trip to Tokyo")]
TOOL = ToolSpec(name="add_lodging", description="Add a stay", parameters={"type": "object"})


def _client(handler: Handler, *, max_attempts: int = 3) -> OllamaClient:
    return OllamaClient(
        host="http://ollama.test",
        model="test-model",
        max_attempts=max_attempts,
        retry_wait_seconds=0,
        client=ollama.AsyncClient(
            host="http://ollama.test", transport=httpx.MockTransport(handler)
        ),
    )


def _chat_part(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"model": "test-model", "message": message, "done": True}


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


async def test_chat_sends_request_and_parses_tool_calls() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        call = {"function": {"name": "add_lodging", "arguments": {"title": "Hotel Okura"}}}
        return httpx.Response(200, json=_chat_part("On it.", [call]))

    reply = await _client(handler).chat(MESSAGES, tools=[TOOL], format={"type": "object"})

    assert reply.content == "On it."
    assert [(c.name, c.arguments) for c in reply.tool_calls] == [
        ("add_lodging", {"title": "Hotel Okura"})
    ]
    body = seen[0]
    assert body["model"] == "test-model"
    assert body["think"] is False
    assert body["options"] == {"num_ctx": 16384, "num_predict": 2048}
    assert body["format"] == {"type": "object"}
    assert body["tools"][0]["function"]["name"] == "add_lodging"
    assert body["messages"] == [{"role": "user", "content": "Plan a trip to Tokyo"}]


async def test_chat_sends_tool_results_back() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_part("Done"))

    await _client(handler).chat([LLMMessage(role="tool", tool_name="add_lodging", content="ok")])

    assert seen[0]["messages"][0]["tool_name"] == "add_lodging"


async def test_chat_retries_transient_errors() -> None:
    responses = iter([httpx.Response(503, text="busy"), httpx.Response(200, json=_chat_part("hi"))])

    reply = await _client(lambda _request: next(responses)).chat(MESSAGES)

    assert reply.content == "hi"


async def test_chat_gives_up_after_max_attempts() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMError, match="Could not reach Ollama"):
        await _client(handler, max_attempts=2).chat(MESSAGES)
    assert calls == 2


async def test_missing_model_is_not_retried_and_explains_how_to_fix() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "model 'test-model' not found"})

    with pytest.raises(LLMError, match="ollama pull test-model"):
        await _client(handler).chat(MESSAGES)
    assert calls == 1


# ---------------------------------------------------------------------------
# stream
# ---------------------------------------------------------------------------


def _ndjson(*parts: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, text="\n".join(json.dumps(part) for part in parts))


async def test_stream_yields_tokens_then_tool_calls() -> None:
    call = {"function": {"name": "add_lodging", "arguments": {"title": "Okura"}}}

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ndjson(_chat_part("Let me "), _chat_part("add that."), _chat_part("", [call]))

    chunks = [chunk async for chunk in _client(handler).stream(MESSAGES, tools=[TOOL])]

    assert "".join(chunk.content for chunk in chunks) == "Let me add that."
    assert [c.name for chunk in chunks for c in chunk.tool_calls] == ["add_lodging"]


async def test_stream_sends_context_and_output_limits() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ndjson(_chat_part("hi"))

    client = OllamaClient(
        host="http://ollama.test",
        model="test-model",
        context_tokens=8192,
        max_output_tokens=512,
        client=ollama.AsyncClient(
            host="http://ollama.test", transport=httpx.MockTransport(handler)
        ),
    )
    _ = [chunk async for chunk in client.stream(MESSAGES)]

    assert seen[0]["options"] == {"num_ctx": 8192, "num_predict": 512}


async def test_stream_logs_token_usage_from_the_final_part(
    caplog: pytest.LogCaptureFixture,
) -> None:
    final = {**_chat_part("."), "prompt_eval_count": 1622, "eval_count": 40}

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ndjson({**_chat_part("hi"), "done": False}, final)

    with caplog.at_level(logging.DEBUG, logger="shared.llm.ollama_client"):
        _ = [chunk async for chunk in _client(handler).stream(MESSAGES)]

    assert "Ollama stream: prompt=1622 tokens, output=40 tokens" in caplog.messages


async def test_stream_retries_before_the_first_chunk() -> None:
    responses = iter([httpx.Response(500, text="oops"), _ndjson(_chat_part("hello"))])

    chunks = [chunk async for chunk in _client(lambda _r: next(responses)).stream(MESSAGES)]

    assert [chunk.content for chunk in chunks] == ["hello"]


async def test_stream_error_mid_response_raises_llm_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(_chat_part("partial")) + '\n{"error": "boom"}')

    received: list[str] = []
    with pytest.raises(LLMError):
        async for chunk in _client(handler).stream(MESSAGES):
            received.append(chunk.content)
    assert received == ["partial"]
