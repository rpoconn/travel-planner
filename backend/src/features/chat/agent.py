"""The planning agent: stream the model's reply, run the tools it calls, feed the results
back, and repeat until it answers in plain text (or the round limit is hit).

Read tools (web_search) run immediately. Write tools only validate: each change is staged
into the caller's `staged` list, which becomes the reply's proposal card. Nothing in the
plan changes during a turn, so the plan JSON in the system prompt stays current. A card
that adds a flight out without one home gets the model a reminder to propose it or ask,
and a reply that says it proposed something without calling a tool gets one to call it.

With a `review` callback, the staged changes are checked once the model stops calling
tools (see features.chat.review). Rejected changes are withdrawn from the card; the model
is told why and gets one more try, after which anything still rejected is withdrawn with a
note to the user.
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
    PROMISED_SEARCH_NUDGE,
    agent_system_prompt,
    review_feedback,
    withdrawn_note,
)
from features.chat.review import ReviewFn
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

# How many times the model may propose again after the reviewer withdrew changes.
REVIEW_RETRIES = 1

# Inserted between the text of two rounds ("Let me add that." ... "Done!").
_ROUND_SEPARATOR = "\n\n"

# A reply claiming a card: "I've proposed …" (straight or curly apostrophe), or a copy of
# the history's "[Proposed changes (…)]" note.
_CLAIMED_PROPOSAL = re.compile(
    r"\bI(?:['\u2019]ve| have)?\s+(?:just\s+)?proposed\b|\[Proposed changes\b", re.IGNORECASE
)
# "Let me search for \u2026", "I should first search \u2026", "I'll look it up": a search announced
# in text instead of called (see PROMISED_SEARCH_NUDGE).
_PROMISED_SEARCH = re.compile(
    r"\b(?:let me|I['\u2019]ll|I will|I need to|I should)\s+"
    r"(?:first\s+|now\s+|quickly\s+|go ahead and\s+)?"
    r"(?:search|look\s+(?:it\s+|that\s+|this\s+|them\s+)?up|do\s+a\s+(?:quick\s+)?search)\b",
    re.IGNORECASE,
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


def _base_call_id(call_id: str) -> str:
    # A bundle tool stages "call_x", "call_x-2", …: one tool call, withdrawn as a unit.
    return call_id.split("-")[0]


def _withdraw(
    staged: list[ProposedChange], rejected: list[tuple[int, str]]
) -> list[tuple[str, str, str]]:
    """Remove the rejected changes from `staged`, each with the rest of its tool call's
    bundle. Returns (base call id, summary, reason) per withdrawn tool call, in order."""
    reasons: dict[str, str] = {}
    for position, reason in rejected:
        reasons.setdefault(_base_call_id(staged[position].call_id), reason)
    withdrawn: dict[str, list[str]] = {}
    for change in staged:
        base = _base_call_id(change.call_id)
        if base in reasons:
            withdrawn.setdefault(base, []).append(change.summary)
    staged[:] = [change for change in staged if _base_call_id(change.call_id) not in reasons]
    return [(base, "; ".join(summaries), reasons[base]) for base, summaries in withdrawn.items()]


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
    review: ReviewFn | None = None,
) -> AsyncIterator[ChatEvent]:
    """Yield `token`, `tool_call` and `tool_result` events for one turn.

    Validated plan changes are appended to `staged` (an out-parameter, in call order);
    their `tool_result` events have `proposed=True`. Raises `LLMError` if the model fails;
    tool failures are reported to the model instead. A change the `review` withdraws gets a
    second `tool_result` for its call, with `ok=False`.
    """
    # The tools see the card as it grows (the same list), so its changes count towards the
    # budget: three dinners proposed together can't each fit on their own. Likewise the
    # evidence list collects this turn's web results, which a cited source_url must be one
    # of (or one known from before, or a link in the user's message).
    ctx = replace(
        ctx,
        staged=staged,
        evidence=[],
        known_sources=ctx.known_sources | tools.urls_in(latest),
    )
    messages = [
        LLMMessage(role="system", content=agent_system_prompt(plan, today)),
        *history,
        LLMMessage(role="user", content=latest),
    ]
    wrote_text = False
    nudged_return = False
    nudged_phantom = False
    nudged_search = False
    reviews_done = 0
    # What the last review let through, so an unchanged card isn't reviewed again.
    reviewed: list[ProposedChange] = []
    # Tool names by call id, for the event that withdraws a call's changes.
    call_names: dict[str, str] = {}

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

        # ---- "Let me search …" with no tool call: have the model search, once ----
        promised = not calls and not final_round and not nudged_search
        if promised and _PROMISED_SEARCH.search(content):
            nudged_search = True
            logger.info("Reply on plan %s promises a search it never made", ctx.plan_id)
            messages.append(LLMMessage(role="assistant", content=content))
            messages.append(LLMMessage(role="system", content=PROMISED_SEARCH_NUDGE))
            continue

        if not calls or final_round:
            if review is None or not staged or staged == reviewed or reviews_done > REVIEW_RETRIES:
                return
            # ---- review the card; withdraw what doesn't fit, and retry once ----
            reviews_done += 1
            rejected = await review(latest, list(staged))
            if not rejected:
                return
            withdrawn = _withdraw(staged, rejected)
            reviewed = list(staged)
            logger.info("Reviewer withdrew %d call(s) on plan %s", len(withdrawn), ctx.plan_id)
            for call_id, summary, reason in withdrawn:
                yield ToolResultEvent(
                    call_id=call_id,
                    name=call_names.get(call_id, ""),
                    ok=False,
                    summary=f"Withdrawn: {summary} — {reason}",
                    proposed=False,
                )
            notes = [(summary, reason) for _, summary, reason in withdrawn]
            if final_round or reviews_done > REVIEW_RETRIES:
                # No retry left: tell the user, after whatever the model already said.
                separator = _ROUND_SEPARATOR if wrote_text else ""
                yield TokenEvent(text=separator + withdrawn_note(notes))
                return
            messages.append(LLMMessage(role="assistant", content=content))
            messages.append(LLMMessage(role="system", content=review_feedback(notes)))
            continue

        # ---- run (or stage) the requested tools and feed the results back ----
        messages.append(LLMMessage(role="assistant", content=content, tool_calls=calls))
        for call in calls:
            call_id = new_call_id()
            call_names[call_id] = call.name
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
