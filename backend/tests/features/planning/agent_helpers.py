"""Helpers for the planner's loop and tool tests: run the loop, call one tool, and set a
plan up the way a turn would find it."""

from pydantic import JsonValue

from features.planning.agent import loop
from features.planning.agent.context import TurnBrief
from features.planning.agent.tools import (
    ToolOutcome,
    TurnContext,
    TurnState,
    available,
    run_tool,
    snapshot,
    store,
)
from features.planning.events import PlanningEvent, PlanningMessageEvent
from features.planning.schemas import TravelPlan
from features.plans import service as plans_service
from features.plans.schemas import (
    ActivityPreferences,
    FlightPreferences,
    LodgingPreferences,
    PlanPreferences,
    PlanUpdate,
)
from shared.llm.fake import FakeRequest
from shared.llm.types import LLMToolCall

PREFERENCES = PlanPreferences(
    flights=FlightPreferences(),
    lodging=LodgingPreferences(hotel_class="midrange"),
    activities=ActivityPreferences(interests=["museum"]),
)


async def plan_of(ctx: TurnContext) -> TravelPlan:
    return await snapshot(ctx)


async def set_preferences(ctx: TurnContext) -> None:
    """Answer every preference, so the searches run."""
    await plans_service.update_preferences(ctx.session, ctx.user_id, ctx.plan_id, PREFERENCES)


async def touch(ctx: TurnContext) -> None:
    """Change the plan (its notes), so its version moves on."""
    await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, PlanUpdate(notes="!"))


async def call(state: TurnState, name: str, **arguments: JsonValue) -> ToolOutcome:
    """Run one tool call as a round that isn't the last would."""
    allowed = available(state, await snapshot(state.ctx), last_round=False)
    return await run_tool(state, LLMToolCall(name=name, arguments=arguments), allowed)


async def go(ctx: TurnContext, *, focus: str | None = None) -> list[PlanningEvent]:
    """A turn the user started from the interface."""
    return [event async for event in loop.run(ctx, focus=focus)]


async def say(ctx: TurnContext, content: str) -> list[PlanningEvent]:
    """A turn the user typed: the text is stored first, as the turn does."""
    await store(ctx, "user", content)
    return [event async for event in loop.run(ctx, said=content)]


def brief_of(request: FakeRequest) -> TurnBrief:
    """What a request of the loop told the model: its first user message."""
    system, user = request.messages[:2]
    assert (system.role, user.role) == ("system", "user")
    return TurnBrief.model_validate_json(user.content)


def tools_of(request: FakeRequest) -> list[str]:
    return [tool.name for tool in request.tools]


def stored(events: list[PlanningEvent]) -> list[tuple[str, str]]:
    """(kind, content) of each message the events stored, in order."""
    return [
        (e.message.kind, e.message.content) for e in events if isinstance(e, PlanningMessageEvent)
    ]
