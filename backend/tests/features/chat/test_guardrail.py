import pytest

from features.chat.guardrail import MAX_ATTEMPTS, check_topic
from features.chat.prompts import LATEST_MESSAGE_MARKER
from features.chat.schemas import GuardrailVerdict
from shared.llm.fake import FakeLLM, json_reply
from shared.llm.types import LLMError, LLMMessage


def _verdict(on_topic: bool, confidence: float = 0.9, topic: str = "x") -> GuardrailVerdict:
    return GuardrailVerdict(on_topic=on_topic, confidence=confidence, topic=topic)


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


async def test_travel_message_is_allowed() -> None:
    llm = FakeLLM([json_reply(_verdict(True, topic="hotels in Tokyo"))])

    decision = await check_topic(llm, [], "Find me a hotel in Shinjuku")

    assert decision.allowed
    assert decision.topic == "hotels in Tokyo"
    assert decision.verdict is not None


async def test_off_topic_message_is_rejected() -> None:
    llm = FakeLLM([json_reply(_verdict(False, topic="python"))])

    decision = await check_topic(llm, [], "Write me a Python sort function")

    assert not decision.allowed
    assert decision.topic == "python"


async def test_unsure_off_topic_verdict_is_allowed() -> None:
    llm = FakeLLM([json_reply(_verdict(False, confidence=0.3))])

    decision = await check_topic(llm, [], "hmm")

    assert decision.allowed


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


async def test_request_uses_verdict_schema_and_includes_history() -> None:
    llm = FakeLLM([json_reply(_verdict(True))])
    history = [
        LLMMessage(role="user", content="Hotels in Tokyo?"),
        LLMMessage(role="assistant", content="1. Okura 2. Park Hyatt"),
    ]

    await check_topic(llm, history, "the second one")

    (request,) = llm.requests
    assert request.format == GuardrailVerdict.model_json_schema()
    assert request.tools == []
    assert request.messages[0].role == "system"
    prompt = request.messages[-1].content
    assert "Assistant: 1. Okura 2. Park Hyatt" in prompt
    assert prompt.endswith(f"{LATEST_MESSAGE_MARKER}\nthe second one")


# ---------------------------------------------------------------------------
# Malformed output / failures
# ---------------------------------------------------------------------------


async def test_malformed_output_is_retried() -> None:
    llm = FakeLLM([json_reply("not json"), json_reply(_verdict(False))])

    decision = await check_topic(llm, [], "What's 2 + 2?")

    assert not decision.allowed
    assert len(llm.requests) == 2


async def test_repeated_malformed_output_fails_open() -> None:
    llm = FakeLLM([json_reply({"on_topic": "maybe"})] * MAX_ATTEMPTS)

    decision = await check_topic(llm, [], "What's 2 + 2?")

    assert decision.allowed
    assert decision.verdict is None
    assert llm.pending == 0


async def test_model_errors_propagate() -> None:
    llm = FakeLLM([LLMError("Ollama is down")])

    with pytest.raises(LLMError, match="Ollama is down"):
        await check_topic(llm, [], "Plan a trip")
