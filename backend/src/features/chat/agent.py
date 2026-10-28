"""The planning agent: stream the model's reply, run the tools it calls, feed the results
back, and repeat until it answers in plain text (or the round limit is hit)."""

import logging
from collections.abc import AsyncIterator
from datetime import date
from uuid import uuid4

from features.chat import tools
from features.chat.prompts import AGENT_FINAL_ANSWER, agent_system_prompt
from features.chat.schemas import (
    ChatEvent,
    PlanUpdatedEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from features.itinerary import service as itinerary_service
from features.plans.schemas import TravelPlan
from shared.llm.types import LLMClient, LLMMessage, LLMToolCall

logger = logging.getLogger(__name__)

# Tool-calling rounds per user message. After the last one the model gets one more call
# with no tools, so it always ends with a text answer.
MAX_TOOL_ROUNDS = 6

# Inserted between the text of two rounds ("Let me add that." ... "Done!").
_ROUND_SEPARATOR = "\n\n"


def _call_id() -> str:
    # Ollama doesn't assign ids to tool calls; these only pair tool_call/tool_result events.
    return f"call_{uuid4().hex[:12]}"


async def run_agent(
    *,
    llm: LLMClient,
    ctx: tools.ToolContext,
    plan: TravelPlan,
    history: list[LLMMessage],
    latest: str,
    today: date,
) -> AsyncIterator[ChatEvent]:
    """Yield `token`, `tool_call`, `tool_result` and `plan_updated` events for one turn.

    Raises `LLMError` if the model fails; tool failures are reported to the model instead.
    """
    messages = [
        LLMMessage(role="system", content=agent_system_prompt(plan, today)),
        *history,
        LLMMessage(role="user", content=latest),
    ]
    wrote_text = False

    for round_index in range(MAX_TOOL_ROUNDS + 1):
        final_round = round_index == MAX_TOOL_ROUNDS
        if final_round:
            logger.warning("Agent hit %d tool rounds on plan %s", MAX_TOOL_ROUNDS, ctx.plan_id)
            messages.append(LLMMessage(role="system", content=AGENT_FINAL_ANSWER))

        # ---- stream this round's reply ----
        content = ""
        calls: list[LLMToolCall] = []
        async for chunk in llm.stream(messages, tools=None if final_round else tools.TOOL_SPECS):
            if chunk.content:
                if not content and wrote_text:
                    yield TokenEvent(text=_ROUND_SEPARATOR)
                content += chunk.content
                yield TokenEvent(text=chunk.content)
            calls.extend(chunk.tool_calls)
        wrote_text = wrote_text or bool(content.strip())

        if not calls or final_round:
            return

        # ---- run the requested tools and feed the results back ----
        messages.append(LLMMessage(role="assistant", content=content, tool_calls=calls))
        plan_changed = False
        for call in calls:
            call_id = _call_id()
            yield ToolCallEvent(call_id=call_id, name=call.name, arguments=call.arguments)
            outcome = await tools.execute(ctx, call)
            yield ToolResultEvent(
                call_id=call_id, name=call.name, ok=outcome.ok, summary=outcome.summary
            )
            if outcome.plan_changed:
                plan = await itinerary_service.get_plan_snapshot(
                    ctx.session, ctx.user_id, ctx.plan_id
                )
                plan_changed = True
                yield PlanUpdatedEvent(plan=plan)
            messages.append(LLMMessage(role="tool", tool_name=call.name, content=outcome.content))

        if plan_changed:
            # Keep the plan JSON in the system prompt current for the next round.
            messages[0] = LLMMessage(role="system", content=agent_system_prompt(plan, today))
