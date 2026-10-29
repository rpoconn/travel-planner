"""The planning agent: stream the model's reply, run the tools it calls, feed the results
back, and repeat until it answers in plain text (or the round limit is hit).

Read tools (web_search) run immediately. Write tools only validate: each change is staged
into the caller's `staged` list, which becomes the reply's proposal card. Nothing in the
plan changes during a turn, so the plan JSON in the system prompt stays current. A card
that adds a flight out without one home gets the model a reminder to propose it or ask,
and a reply that says it proposed something without calling a tool gets one to call it.
"""

import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import date
from uuid import uuid4

from features.chat import tools
from features.chat.prompts import (
    AGENT_FINAL_ANSWER,
    ALREADY_STAGED,
    PHANTOM_PROPOSAL_NUDGE,
    agent_system_prompt,
)
from features.chat.schemas import (
    ChatEvent,
    ProposedChange,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from features.plans.schemas import TravelPlan
from shared.llm.types import LLMClient, LLMMessage, LLMToolCall, ToolSpec

logger = logging.getLogger(__name__)

# Tool-calling rounds per user message. After the last one the model gets one more call
# with no tools, so it always ends with a text answer.
MAX_TOOL_ROUNDS = 6

# Inserted between the text of two rounds ("Let me add that." ... "Done!").
_ROUND_SEPARATOR = "\n\n"

# A reply claiming a card: "I've proposed …" (straight or curly apostrophe), or a copy of
# the history's "[Proposed changes (…)]" note.
_CLAIMED_PROPOSAL = re.compile(
    r"\bI(?:['\u2019]ve| have)?\s+(?:just\s+)?proposed\b|\[Proposed changes\b", re.IGNORECASE
)


def new_call_id() -> str:
    # Ollama doesn't assign ids to tool calls; these only pair tool_call/tool_result events.
    return f"call_{uuid4().hex[:12]}"


def _stage(
    outcome: tools.ToolOutcome, staged: list[ProposedChange], call_id: str
) -> tools.ToolOutcome:
    """Add the prepared changes to `staged`, or tell the model they're already there.
    (Small models often re-send a change; arguments are normalised, so a repeat compares
    equal.) A round trip re-sent after its outbound leg was staged adds only the return."""
    fresh = [
        prepared
        for prepared in outcome.prepared
        if not any(c.name == prepared.name and c.arguments == prepared.arguments for c in staged)
    ]
    if outcome.prepared and not fresh:
        summary = "; ".join(prepared.summary for prepared in outcome.prepared)
        return tools.ToolOutcome(
            ok=True, content=ALREADY_STAGED, summary=f"Already proposed: {summary}"
        )
    for index, prepared in enumerate(fresh):
        staged.append(
            ProposedChange(
                # One call can stage two changes; the card needs a distinct id for each.
                call_id=call_id if index == 0 else f"{call_id}-{index + 1}",
                name=prepared.name,
                arguments=prepared.arguments,
                summary=prepared.summary,
            )
        )
    return outcome


async def run_agent(
    *,
    llm: LLMClient,
    ctx: tools.ToolContext,
    plan: TravelPlan,
    history: list[LLMMessage],
    latest: str,
    today: date,
    staged: list[ProposedChange],
    tool_specs: Sequence[ToolSpec] = tools.TOOL_SPECS,
) -> AsyncIterator[ChatEvent]:
    """Yield `token`, `tool_call` and `tool_result` events for one turn.

    Validated plan changes are appended to `staged` (an out-parameter, in call order);
    their `tool_result` events have `proposed=True`. Raises `LLMError` if the model fails;
    tool failures are reported to the model instead.
    """
    # The tools see the card as it grows (the same list), so its changes count towards the
    # budget: three dinners proposed together can't each fit on their own.
    ctx = replace(ctx, staged=staged)
    messages = [
        LLMMessage(role="system", content=agent_system_prompt(plan, today)),
        *history,
        LLMMessage(role="user", content=latest),
    ]
    wrote_text = False
    nudged_return = False
    nudged_phantom = False

    for round_index in range(MAX_TOOL_ROUNDS + 1):
        final_round = round_index == MAX_TOOL_ROUNDS
        if final_round:
            logger.warning("Agent hit %d tool rounds on plan %s", MAX_TOOL_ROUNDS, ctx.plan_id)
            messages.append(LLMMessage(role="system", content=AGENT_FINAL_ANSWER))

        # ---- stream this round's reply ----
        content = ""
        calls: list[LLMToolCall] = []
        async for chunk in llm.stream(messages, tools=None if final_round else tool_specs):
            if chunk.content:
                if not content and wrote_text:
                    yield TokenEvent(text=_ROUND_SEPARATOR)
                content += chunk.content
                yield TokenEvent(text=chunk.content)
            calls.extend(chunk.tool_calls)
        wrote_text = wrote_text or bool(content.strip())

        # ---- "I've proposed …" with no card: have the model make the tool calls, once ----
        phantom = not calls and not final_round and not staged and not nudged_phantom
        if phantom and _CLAIMED_PROPOSAL.search(content):
            nudged_phantom = True
            logger.info("Reply on plan %s claims a proposal it never made", ctx.plan_id)
            messages.append(LLMMessage(role="assistant", content=content))
            messages.append(LLMMessage(role="system", content=PHANTOM_PROPOSAL_NUDGE))
            continue

        if not calls or final_round:
            return

        # ---- run (or stage) the requested tools and feed the results back ----
        messages.append(LLMMessage(role="assistant", content=content, tool_calls=calls))
        for call in calls:
            call_id = new_call_id()
            yield ToolCallEvent(call_id=call_id, name=call.name, arguments=call.arguments)
            outcome = _stage(await tools.execute(ctx, call), staged, call_id)
            yield ToolResultEvent(
                call_id=call_id,
                name=call.name,
                ok=outcome.ok,
                summary=outcome.summary,
                proposed=bool(outcome.prepared),
            )
            messages.append(LLMMessage(role="tool", tool_name=call.name, content=outcome.content))

        # ---- a flight out with none home: have the model propose it (or ask), once ----
        if not nudged_return:
            nudge = tools.return_flight_nudge(plan, staged)
            if nudge is not None:
                nudged_return = True
                logger.info("Proposal on plan %s has no return flight; nudging", ctx.plan_id)
                messages.append(LLMMessage(role="system", content=nudge))
