"""`LLMClient` backed by a local Ollama server, with retries on transient failures."""

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx
import ollama
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from shared.llm.types import LLMChunk, LLMError, LLMMessage, LLMToolCall, ToolSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def _is_transient(exc: BaseException) -> bool:
    # The ollama library raises the builtin ConnectionError for refused connections on
    # non-streaming calls, but lets httpx errors through for streaming ones.
    if isinstance(exc, ConnectionError | httpx.TransportError):
        return True
    if isinstance(exc, ollama.ResponseError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def _describe(exc: BaseException, model: str, host: str) -> str:
    if isinstance(exc, ollama.ResponseError) and exc.status_code == 404:
        return f"Model '{model}' is not available in Ollama. Run `ollama pull {model}`."
    if isinstance(exc, ConnectionError | httpx.ConnectError):
        return f"Could not reach Ollama at {host}. Is `ollama serve` running?"
    if isinstance(exc, httpx.TimeoutException):
        return "The local model took too long to respond."
    return f"The local model failed: {exc}"


# ---------------------------------------------------------------------------
# Wire format conversion
# ---------------------------------------------------------------------------


def _to_wire(message: LLMMessage) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {"function": {"name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    if message.tool_name is not None:
        wire["tool_name"] = message.tool_name
    return wire


def _tool_to_wire(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _log_usage(kind: str, response: ollama.ChatResponse) -> None:
    # Only the final part of a response carries the counts. A prompt near `num_ctx` is the
    # first sign of an overflow (Ollama silently truncates, and tool calls get garbled).
    if response.prompt_eval_count is not None:
        logger.debug(
            "Ollama %s: prompt=%d tokens, output=%d tokens",
            kind,
            response.prompt_eval_count,
            response.eval_count or 0,
        )


def _tool_calls(message: ollama.Message) -> list[LLMToolCall]:
    return [
        LLMToolCall(name=call.function.name, arguments=dict(call.function.arguments))
        for call in message.tool_calls or []
    ]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OllamaClient:
    def __init__(
        self,
        *,
        host: str,
        model: str,
        think: bool = False,
        timeout_seconds: float = 120.0,
        context_tokens: int = 16384,
        max_output_tokens: int = 2048,
        max_attempts: int = 3,
        retry_wait_seconds: float = 0.5,
        client: ollama.AsyncClient | None = None,
    ) -> None:
        self._host = host
        self._model = model
        self._think = think
        self._options = {"num_ctx": context_tokens, "num_predict": max_output_tokens}
        self._max_attempts = max_attempts
        self._retry_wait_seconds = retry_wait_seconds
        self._client = client or ollama.AsyncClient(host=host, timeout=timeout_seconds)

    def _retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._retry_wait_seconds, max=8),
            retry=retry_if_exception(_is_transient),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    def _request(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[ToolSpec] | None,
    ) -> Mapping[str, Any]:
        return {
            "model": self._model,
            "messages": [_to_wire(message) for message in messages],
            "tools": [_tool_to_wire(tool) for tool in tools] if tools else None,
            "think": self._think,
            "options": self._options,
        }

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        format: dict[str, Any] | None = None,
    ) -> LLMMessage:
        try:
            async for attempt in self._retrying():
                with attempt:
                    response = await self._client.chat(
                        **self._request(messages, tools), format=format, stream=False
                    )
        except Exception as exc:
            logger.exception("Ollama chat call failed")
            raise LLMError(_describe(exc, self._model, self._host)) from exc

        _log_usage("chat", response)
        return LLMMessage(
            role="assistant",
            content=response.message.content or "",
            tool_calls=_tool_calls(response.message),
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[LLMChunk]:
        # The HTTP request only happens once the stream is iterated, so the retry covers
        # opening the stream and reading the first chunk. Failures after tokens have been
        # sent to the user are not retried (they'd be duplicated).
        try:
            async for attempt in self._retrying():
                with attempt:
                    parts = await self._client.chat(**self._request(messages, tools), stream=True)
                    first = await anext(parts, None)
        except Exception as exc:
            logger.exception("Ollama stream failed to start")
            raise LLMError(_describe(exc, self._model, self._host)) from exc

        if first is None:
            return
        _log_usage("stream", first)
        yield LLMChunk(content=first.message.content or "", tool_calls=_tool_calls(first.message))
        try:
            async for part in parts:
                _log_usage("stream", part)
                yield LLMChunk(
                    content=part.message.content or "", tool_calls=_tool_calls(part.message)
                )
        except Exception as exc:
            logger.exception("Ollama stream failed mid-response")
            raise LLMError(_describe(exc, self._model, self._host)) from exc

    async def aclose(self) -> None:
        await self._client.close()  # type: ignore[no-untyped-call]  # untyped upstream
