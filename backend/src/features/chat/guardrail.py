"""Topic guardrail: classify each user message before the agent sees it, and write the
gentle redirect when it's off-topic."""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import ValidationError

from features.chat.prompts import guardrail_messages, redirect_messages
from features.chat.schemas import GuardrailVerdict
from features.plans.schemas import TravelPlan
from shared.llm.types import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

# How many times to ask again when the model's JSON doesn't match the schema.
MAX_ATTEMPTS = 2
# Off-topic verdicts below this confidence are let through: a wrong redirect is more
# annoying than an occasional off-topic answer.
OFF_TOPIC_MIN_CONFIDENCE = 0.5

_VERDICT_SCHEMA = GuardrailVerdict.model_json_schema()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    topic: str
    verdict: GuardrailVerdict | None  # None when the classifier failed and we failed open


async def check_topic(llm: LLMClient, history: list[LLMMessage], latest: str) -> GuardrailDecision:
    """Classify `latest` (with `history` for context). Fails open on malformed output.

    `LLMError` (model unreachable) is not caught: the agent would fail the same way, so the
    caller reports it once.
    """
    messages = guardrail_messages(history, latest)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = await llm.chat(messages, format=_VERDICT_SCHEMA)
        try:
            verdict = GuardrailVerdict.model_validate_json(reply.content)
        except ValidationError as exc:
            logger.warning(
                "Guardrail returned malformed output (attempt %d/%d): %s",
                attempt,
                MAX_ATTEMPTS,
                exc.errors(include_url=False)[:3],
            )
            continue
        allowed = verdict.on_topic or verdict.confidence < OFF_TOPIC_MIN_CONFIDENCE
        logger.info(
            "Guardrail: on_topic=%s confidence=%.2f topic=%r -> %s",
            verdict.on_topic,
            verdict.confidence,
            verdict.topic,
            "allowed" if allowed else "redirect",
        )
        return GuardrailDecision(allowed=allowed, topic=verdict.topic, verdict=verdict)

    logger.error("Guardrail failed %d times; failing open", MAX_ATTEMPTS)
    return GuardrailDecision(allowed=True, topic="", verdict=None)


# ---------------------------------------------------------------------------
# Redirect
# ---------------------------------------------------------------------------


async def stream_redirect(llm: LLMClient, plan: TravelPlan, latest: str) -> AsyncIterator[str]:
    """Stream a short, friendly reply that steers back to the trip (no tools)."""
    async for chunk in llm.stream(redirect_messages(plan, latest)):
        if chunk.content:
            yield chunk.content
