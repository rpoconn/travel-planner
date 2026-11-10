"""`LLMClient` backed by the Claude API (Anthropic), with the SDK's built-in retries."""

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict, cast

import anthropic
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaMessage,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaToolParam,
)
from pydantic import JsonValue, TypeAdapter

from shared.llm.types import LLMChunk, LLMError, LLMMessage, LLMToolCall, ToolSpec

logger = logging.getLogger(__name__)

# Server-side refusal fallback: if Claude's safety classifiers decline a request, the API
# re-runs it on the model's default fallback model inside the same call. Models without a
# fallback just run alone, so this is safe to send whichever model is configured.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_TOOL_ARGUMENTS = TypeAdapter(dict[str, JsonValue])

Effort = Literal["low", "high"]


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def _describe(exc: BaseException, model: str) -> str:
    # Most specific first: the subclasses of APIStatusError, then the transport errors.
    if isinstance(exc, LLMError):
        return str(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"The Anthropic API key isn't allowed to use '{model}'."
    if isinstance(exc, anthropic.NotFoundError):
        return f"Model '{model}' is not available. Check ANTHROPIC_MODEL in .env."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is rate limited right now. Try again in a minute."
    if isinstance(exc, anthropic.APITimeoutError):
        return "Claude took too long to respond."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the Claude API. Check your internet connection."
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return "The Claude API is having trouble. Try again shortly."
        return f"The Claude API rejected the request: {exc.message}"
    return f"The model failed: {exc}"


def _check_stop(message: BetaMessage) -> None:
    if message.stop_reason == "refusal":
        raise LLMError("The model declined to answer that. Try rephrasing your message.")
    if message.stop_reason == "max_tokens":
        # The reply was cut off; a JSON reply will fail validation and the caller retries.
        logger.warning("Claude reply hit max_tokens (model=%s)", message.model)


def _log_usage(kind: str, message: BetaMessage) -> None:
    logger.debug(
        "Claude %s: model=%s, input=%d tokens, output=%d tokens, stop=%s",
        kind,
        message.model,
        message.usage.input_tokens,
        message.usage.output_tokens,
        message.stop_reason,
    )


# ---------------------------------------------------------------------------
# Wire format conversion
# ---------------------------------------------------------------------------


def _strict(node: JsonValue) -> JsonValue:
    """`node` with `additionalProperties: false` on every object schema, which Claude's
    structured outputs require."""
    if isinstance(node, list):
        return [_strict(value) for value in node]
    if not isinstance(node, dict):
        return node
    strict: dict[str, JsonValue] = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            # Keys here are field names (a field may be called "type"), so only recurse into
            # each field's schema.
            strict[key] = {name: _strict(schema) for name, schema in value.items()}
        else:
            strict[key] = _strict(value)
    if strict.get("type") == "object":
        strict.setdefault("additionalProperties", False)
    return strict


def strict_schema(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    strict = _strict(schema)
    assert isinstance(strict, dict)  # an object stays an object
    return strict


# Claude compiles a structured-output schema into a grammar and rejects the request (400)
# when the schema has more than these, counted across the whole schema, nested objects
# included. https://platform.claude.com/docs/en/build-with-claude/structured-outputs
MAX_OPTIONAL_FIELDS = 24
MAX_UNION_FIELDS = 16


@dataclass(frozen=True)
class SchemaSize:
    optional: int  # fields not in their object's `required`
    unions: int  # fields that are an `anyOf` or a type list, or a list of one


def _is_union(field: JsonValue) -> bool:
    if not isinstance(field, dict):
        return False
    items = field.get("items")
    return (
        "anyOf" in field
        or isinstance(field.get("type"), list)
        or (isinstance(items, dict) and "anyOf" in items)
    )


def schema_size(node: JsonValue) -> SchemaSize:
    """What `node` counts toward Claude's limits (`MAX_OPTIONAL_FIELDS`, `MAX_UNION_FIELDS`).
    A test holds every structured-output schema to them, so one that grows too big fails
    there rather than as a rejected request."""
    if isinstance(node, list):
        sizes = [schema_size(value) for value in node]
        return SchemaSize(sum(s.optional for s in sizes), sum(s.unions for s in sizes))
    if not isinstance(node, dict):
        return SchemaSize(0, 0)
    optional = unions = 0
    properties = node.get("properties")
    if isinstance(properties, dict):
        required = node.get("required")
        names = set(required) if isinstance(required, list) else set()
        optional += sum(name not in names for name in properties)
        unions += sum(_is_union(field) for field in properties.values())
    # Field names under "properties" aren't schemas, so only its values are walked.
    children = [value for key, value in node.items() if key != "properties"]
    if isinstance(properties, dict):
        children += list(properties.values())
    inner = schema_size(children)
    return SchemaSize(optional + inner.optional, unions + inner.unions)


def _call_blocks(message: LLMMessage, call_ids: Sequence[str]) -> list[BetaContentBlockParam]:
    """An assistant turn that calls tools, as Claude's content blocks."""
    if message.provider_content:
        # Claude's own blocks from `_provider_content`, sent back untouched: they hold the
        # thinking blocks (and their signatures) the API requires with tool results. The
        # cast is safe because that function only builds block params.
        return cast(list[BetaContentBlockParam], message.provider_content)
    blocks: list[BetaContentBlockParam] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call, call_id in zip(message.tool_calls, call_ids, strict=True):
        arguments: dict[str, object] = dict(call.arguments)
        blocks.append({"type": "tool_use", "id": call_id, "name": call.name, "input": arguments})
    return blocks


def _to_wire(messages: Sequence[LLMMessage]) -> tuple[str, list[BetaMessageParam]]:
    """Split `messages` into Claude's top-level system prompt and its message list."""
    system: list[str] = []
    wire: list[BetaMessageParam] = []
    # A call without an id (a hand-built message, or one from Ollama) gets one made up
    # here; a result without `tool_call_id` takes the oldest unanswered call's.
    pending_ids: list[str] = []
    # The run of tool results being collected: Claude wants every result of one assistant
    # turn in a single user message.
    results: list[BetaContentBlockParam] | None = None
    for index, message in enumerate(messages):
        if message.role != "tool":
            results = None
        if message.role == "system":
            system.append(message.content)
        elif message.role == "tool":
            if message.tool_call_id is not None:
                tool_use_id = message.tool_call_id
                if tool_use_id in pending_ids:
                    pending_ids.remove(tool_use_id)
            else:
                tool_use_id = pending_ids.pop(0) if pending_ids else f"call_orphan_{index}"
            result: BetaContentBlockParam = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": message.content,
            }
            if results is None:
                results = [result]
                wire.append({"role": "user", "content": results})
            else:
                results.append(result)
        elif message.role == "assistant" and message.tool_calls:
            call_ids = [
                call.id or f"call_{index}_{call_index}"
                for call_index, call in enumerate(message.tool_calls)
            ]
            pending_ids = list(call_ids)
            wire.append({"role": "assistant", "content": _call_blocks(message, call_ids)})
        elif message.role == "assistant":
            wire.append({"role": "assistant", "content": message.content})
        else:
            wire.append({"role": "user", "content": message.content})
    return "\n\n".join(system), wire


def _tool_to_wire(tool: ToolSpec) -> BetaToolParam:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.parameters),
    }


def _reply(message: BetaMessage) -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content="".join(block.text for block in message.content if block.type == "text"),
        tool_calls=_tool_calls(message),
        provider_content=_provider_content(message),
    )


def _tool_calls(message: BetaMessage) -> list[LLMToolCall]:
    return [
        LLMToolCall(
            id=block.id, name=block.name, arguments=_TOOL_ARGUMENTS.validate_python(block.input)
        )
        for block in message.content
        if block.type == "tool_use"
    ]


def _provider_content(message: BetaMessage) -> list[dict[str, JsonValue]]:
    """A tool-calling reply's blocks as request params, in order, for `_call_blocks` to
    send back. Empty for a reply without tool calls: nothing sends that one back in a
    form that needs its thinking."""
    if not any(block.type == "tool_use" for block in message.content):
        return []
    blocks: list[dict[str, JsonValue]] = []
    for block in message.content:
        if block.type == "thinking":
            blocks.append(
                {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
            )
        elif block.type == "redacted_thinking":
            blocks.append({"type": "redacted_thinking", "data": block.data})
        elif block.type == "text" and block.text:
            blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": _TOOL_ARGUMENTS.validate_python(block.input),
                }
            )
    return blocks


class _Request(TypedDict, total=False):
    """The keyword arguments shared by every `messages.create` / `messages.stream` call."""

    model: str
    max_tokens: int
    system: str
    messages: list[BetaMessageParam]
    tools: list[BetaToolParam]
    output_config: BetaOutputConfigParam
    betas: list[str]
    fallbacks: Literal["default"]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AnthropicClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        think: bool = False,
        max_tokens: int = 16000,
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
        refusal_fallback: bool = True,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._think = think
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._refusal_fallback = refusal_fallback
        self._client = client

    def _sdk(self) -> anthropic.AsyncAnthropic:
        # Built on first use so a missing key is reported to the user on their first
        # message instead of stopping the whole app at startup.
        if self._client is None:
            if not self._api_key:
                raise LLMError("ANTHROPIC_API_KEY is not set. Add it to .env and restart.")
            # The SDK retries connection errors, 408/409/429 and 5xx with backoff (and
            # honors retry-after), so there's no separate retry loop here.
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                max_retries=self._max_attempts - 1,
            )
        return self._client

    def _request(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[ToolSpec] | None,
        format: dict[str, JsonValue] | None = None,
        think: bool | None = None,
    ) -> _Request:
        system, wire = _to_wire(messages)
        # Thinking stays adaptive (current models reject turning it off); effort sets how
        # much of it the model does. "low" keeps extraction and quick answers fast.
        effort: Effort = "high" if (self._think if think is None else think) else "low"
        output_config: BetaOutputConfigParam = {"effort": effort}
        if format is not None:
            schema: dict[str, object] = dict(strict_schema(format))
            output_config["format"] = {"type": "json_schema", "schema": schema}
        request: _Request = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": wire,
            "output_config": output_config,
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [_tool_to_wire(tool) for tool in tools]
        if self._refusal_fallback:
            request["betas"] = [_FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        format: dict[str, JsonValue] | None = None,
        think: bool | None = None,
    ) -> LLMMessage:
        try:
            response = await self._sdk().beta.messages.create(
                **self._request(messages, tools, format, think)
            )
            _log_usage("chat", response)
            _check_stop(response)
        except Exception as exc:
            logger.exception("Claude chat call failed (model=%s)", self._model)
            raise LLMError(_describe(exc, self._model)) from exc
        return _reply(response)

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        think: bool | None = None,
    ) -> AsyncIterator[LLMChunk]:
        # The request (with the SDK's retries) happens when the stream is entered, before
        # any token reaches the user. Failures after that aren't retried: the tokens
        # already shown would be duplicated.
        try:
            request = self._request(messages, tools, think=think)
            async with self._sdk().beta.messages.stream(**request) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield LLMChunk(content=text)
                final = await stream.get_final_message()
            _log_usage("stream", final)
            _check_stop(final)
        except Exception as exc:
            logger.exception("Claude stream failed (model=%s)", self._model)
            raise LLMError(_describe(exc, self._model)) from exc
        tool_calls = _tool_calls(final)
        if tool_calls:
            yield LLMChunk(tool_calls=tool_calls, provider_content=_provider_content(final))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
