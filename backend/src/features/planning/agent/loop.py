"""The planner's loop: the model drives one turn with tools until it needs the user.

Each round sends the conversation so far and the tools the round offers
(`tools.available`), streams the reply, and runs the tools it calls (`tools.run_tool`),
handing each result back. The turn ends when:

- a tool that ends the turn runs (`ask_user`, `present_choices`, `suggest_changes`): the
  user now has a question, choices or a suggestion to answer;
- the model replies with text and no tool call: an answer to a question, or the news that
  the trip is complete. The text is stored as the planner's message. When the user typed
  it under a form and the turn changed nothing, the form is shown again after the answer:
  it is still what the plan needs, and typing over it took it off the screen;
- the rounds run out (`MAX_ROUNDS`; the last one only offers the tools that end the turn)
  or the model says nothing: a fixed message lists what the trip still needs.

A call that can't be made (bad arguments, a broken rule, a tool that isn't offered) comes
back to the model as the tool's result, so it can repair it. `LLMError` is not caught here:
the turn stores it and shows it, as it does today (`turn.run_turn`).
"""

import logging
from collections.abc import AsyncIterator, Sequence

from features.chat import service as chat_service
from features.chat.models import Message
from features.chat.schemas import MessageKind
from features.planning import service as planning_service
from features.planning.agent import prompt
from features.planning.agent.context import TurnBrief, conversation, planner_context
from features.planning.agent.tool_models import tool_specs
from features.planning.agent.tools import (
    TurnContext,
    TurnState,
    available,
    run_tool,
    snapshot,
    status_text,
    store,
)
from features.planning.events import PlanningEvent, PlanningStatusEvent, PlanningTokenEvent
from features.planning.schemas import AskInput, TravelPlan
from shared.llm.types import LLMMessage, add_chunk

logger = logging.getLogger(__name__)

# Model calls per turn. A turn rarely needs more than four (read a change, write it,
# search, show the choices); hitting the limit means the model is going in circles.
MAX_ROUNDS = 8
# Transcript messages the model is shown.
HISTORY = 20

COMPLETE = "Your trip has everything it needs."


def fallback_text(plan: TravelPlan) -> str:
    """What the user is told when the model ends a turn with nothing for them."""
    needs = [problem.message for problem in plan.problems if problem.severity == "blocking"]
    if not needs:
        return COMPLETE
    return f"The trip still needs: {'; '.join(needs)}. Tell me which to work on."


def _asked(rows: Sequence[Message], said: str | None, plan: TravelPlan) -> AskInput | None:
    """The form the user's text was typed under: the message before it, when that showed
    one; for the first thing said in a plan, the form the plan opened on. Failure notices
    in between don't count, as for the plan's `pending`."""
    said_in = [row for row in rows if row.kind != MessageKind.ERROR]
    if said is None or not said_in or said_in[-1].role != "user":
        return None
    if len(said_in) == 1:
        opening = planning_service.opening_form(plan, plan.problems)
        return opening.form if opening is not None else None
    before = said_in[-2]
    if before.kind != MessageKind.EXPECTED_INPUT or before.payload is None:
        return None
    return AskInput.model_validate(before.payload)


def _brief(
    state: TurnState, plan: TravelPlan, rows: Sequence[Message], focus: str | None
) -> TurnBrief:
    ctx = state.ctx
    return TurnBrief(
        conversation=conversation([(row.role, row.content) for row in rows]),
        plan=planner_context(
            plan,
            today=ctx.today,
            searches_left=state.searches_left,
            asked=_asked(rows, state.said, plan),
        ),
        said=state.said,
        focus=focus,
    )


async def run(
    ctx: TurnContext, *, said: str | None = None, focus: str | None = None
) -> AsyncIterator[PlanningEvent]:
    """Drive one turn. `said` is the text the user typed (already stored), if that is what
    started the turn; `focus` the id of the problem they asked to work on. Whatever they
    did through the interface is already written and in the transcript."""
    state = TurnState(ctx=ctx, said=said)
    plan = await snapshot(ctx)
    rows = await chat_service.recent_messages(ctx.session, ctx.plan_id, limit=HISTORY)
    history = prompt.messages(_brief(state, plan, rows, focus))
    started_at = plan.version

    for round_number in range(1, MAX_ROUNDS + 1):
        allowed = available(state, plan, last_round=round_number == MAX_ROUNDS)
        reply = LLMMessage(role="assistant")
        async for chunk in ctx.llm.stream(history, tools=tool_specs(allowed)):
            add_chunk(reply, chunk)
            if chunk.content:
                yield PlanningTokenEvent(text=chunk.content)

        if not reply.tool_calls:
            answer = reply.content.strip()
            if answer:
                yield await store(ctx, "assistant", answer)
                form = _asked(rows, said, plan)
                if form is not None and plan.version == started_at:
                    yield await store(
                        ctx, "assistant", form.prompt, kind=MessageKind.EXPECTED_INPUT, payload=form
                    )
                return
            break

        history.append(reply)
        for call in reply.tool_calls:
            status = status_text(call)
            if status is not None:
                # Also tells the client to drop any text streamed before the call.
                yield PlanningStatusEvent(text=status)
            outcome = await run_tool(state, call, allowed)
            for event in outcome.events:
                yield event
            if outcome.ends_turn:
                return
            history.append(
                LLMMessage(
                    role="tool",
                    tool_name=call.name,
                    tool_call_id=call.id,
                    content=outcome.result.model_dump_json(),
                )
            )
        plan = await snapshot(ctx)
    else:
        logger.warning("Plan %s: the planner was still going after %d rounds", plan.id, MAX_ROUNDS)

    yield await store(ctx, "assistant", fallback_text(await snapshot(ctx)), kind=MessageKind.NUDGE)
