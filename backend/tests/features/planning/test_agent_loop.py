"""The planner's loop, with a scripted model: what it sends, how tool results go back, and
how a turn ends.

The plan has every basic and nothing else (`conftest.turn`).
"""

import pytest
from pydantic import JsonValue

from features.chat.schemas import MessageKind
from features.planning.agent.context import FormView
from features.planning.agent.loop import MAX_ROUNDS
from features.planning.agent.tool_models import ToolFailure, ValidationResult
from features.planning.agent.tools import TurnContext, store
from features.planning.events import PlanningStatusEvent, PlanningTokenEvent
from features.planning.pending import ask_input
from features.planning.schemas import AskInput, OfferChoice
from features.plans.schemas import SlotName
from shared.llm.fake import FakeLLM, text, tool_calls
from shared.llm.types import LLMError
from tests.features.planning.agent_helpers import (
    brief_of,
    go,
    plan_of,
    say,
    set_preferences,
    stored,
    tools_of,
)

ENDING = ["ask_user", "present_choices", "suggest_changes"]
ASK_DATES = tool_calls(("ask_user", {"question": "When are you going?", "slot": "dates"}))
VALIDATE = tool_calls(("validate_plan", {}))


# ---------------------------------------------------------------------------
# What the model is sent
# ---------------------------------------------------------------------------


async def test_the_brief_holds_the_conversation_the_plan_and_the_trigger(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(text("May is mild and dry."))
    await store(turn, "assistant", "Where to next?")
    await store(turn, "system", "Something went wrong.", kind=MessageKind.ERROR)

    await say(turn, "What's Lisbon like in May?")

    brief = brief_of(fake_llm.requests[0])
    assert [(line.who, line.text) for line in brief.conversation] == [
        ("planner", "Where to next?"),
        ("user", "What's Lisbon like in May?"),
    ]
    assert brief.said == "What's Lisbon like in May?"
    assert brief.plan.trip.destination == "Lisbon, Portugal"
    assert brief.plan.problems[0].id == "missing_outbound"
    assert brief.plan.searches_left == 2 and not brief.plan.complete


async def test_typed_text_is_read_against_the_form_it_came_in_under(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(text("Noted."), text("Noted."))
    form = ask_input(SlotName.FLIGHT_PREFERENCES, await plan_of(turn))
    await store(turn, "assistant", form.prompt, kind=MessageKind.EXPECTED_INPUT, payload=form)

    events = await say(turn, "What's the difference?")

    asked = FormView(slot=SlotName.FLIGHT_PREFERENCES, question="How do you like to fly?")
    assert brief_of(fake_llm.requests[0]).plan.pending == asked
    # The text took the form's place at the end of the transcript, and the planner only
    # answered: the form is put back after the answer.
    assert stored(events) == [
        (MessageKind.MESSAGE, "Noted."),
        (MessageKind.EXPECTED_INPUT, "How do you like to fly?"),
    ]
    assert (await plan_of(turn)).pending == form

    await go(turn)
    assert brief_of(fake_llm.requests[1]).plan.pending == asked


async def test_a_form_is_not_put_back_once_the_turn_changed_the_plan(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    form = ask_input(SlotName.FLIGHT_PREFERENCES, await plan_of(turn))
    await store(turn, "assistant", form.prompt, kind=MessageKind.EXPECTED_INPUT, payload=form)
    change: dict[str, JsonValue] = {"type": "set_flight_preferences", "cabin": "economy"}
    fake_llm.queue(
        tool_calls(("apply_user_changes", {"quote": "economy", "changes": [change]})),
        text("Economy it is."),
    )

    events = await say(turn, "Economy, please")

    assert [kind for kind, _ in stored(events)] == [MessageKind.CHANGES, MessageKind.MESSAGE]
    assert (await plan_of(turn)).pending is None


async def test_the_problem_to_work_on_is_passed_along(turn: TurnContext, fake_llm: FakeLLM) -> None:
    fake_llm.queue(text("On it."))

    await go(turn, focus="no_activities")

    brief = brief_of(fake_llm.requests[0])
    assert (brief.said, brief.focus) == (None, "no_activities")


async def test_only_a_typed_turn_is_offered_a_writing_tool(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(text("Hello."), text("Hello."))

    await go(turn)
    await say(turn, "hi")

    clicked, typed = (tools_of(request) for request in fake_llm.requests)
    assert "apply_user_changes" not in clicked
    assert typed == [*clicked, "apply_user_changes"]


# ---------------------------------------------------------------------------
# How a turn ends
# ---------------------------------------------------------------------------


async def test_a_text_reply_is_streamed_and_stored(turn: TurnContext, fake_llm: FakeLLM) -> None:
    fake_llm.queue(text("May is mild and dry."))

    events = await say(turn, "What's Lisbon like in May?")

    tokens = [event.text for event in events if isinstance(event, PlanningTokenEvent)]
    assert "".join(tokens) == "May is mild and dry."
    assert stored(events) == [(MessageKind.MESSAGE, "May is mild and dry.")]
    assert fake_llm.requests[0].streamed


async def test_a_tool_that_ends_the_turn_stops_the_loop(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    # The second call of the same reply is never run, and the model isn't asked again.
    fake_llm.queue(
        tool_calls(
            ("ask_user", {"question": "When are you going?", "slot": "dates"}),
            ("search", {"kind": "activities"}),
        )
    )

    events = await go(turn)

    assert stored(events) == [(MessageKind.EXPECTED_INPUT, "When are you going?")]
    assert len(fake_llm.requests) == 1
    assert isinstance((await plan_of(turn)).pending, AskInput)


async def test_tool_results_go_back_to_the_model(turn: TurnContext, fake_llm: FakeLLM) -> None:
    fake_llm.queue(VALIDATE, ASK_DATES)

    events = await go(turn)

    assert [e.text for e in events if isinstance(e, PlanningStatusEvent)] == ["Checking the trip…"]
    first, second = fake_llm.requests
    assert second.messages[: len(first.messages)] == first.messages
    reply, result = second.messages[-2:]
    assert reply.tool_calls[0].name == "validate_plan"
    assert (result.role, result.tool_name) == ("tool", "validate_plan")
    assert not ValidationResult.model_validate_json(result.content).complete
    assert stored(events) == [(MessageKind.EXPECTED_INPUT, "When are you going?")]


async def test_a_search_then_its_choices(turn: TurnContext, fake_llm: FakeLLM) -> None:
    await set_preferences(turn)
    fake_llm.queue(
        tool_calls(("search", {"kind": "flight_out"})),
        tool_calls(("present_choices", {"search": "a"})),
    )

    events = await go(turn)

    statuses = [e.text for e in events if isinstance(e, PlanningStatusEvent)]
    assert statuses == ["Finding flights out…"]
    assert stored(events) == [(MessageKind.CHOICES, "Pick your flight out.")]
    assert isinstance((await plan_of(turn)).pending, OfferChoice)


async def test_a_call_that_does_not_fit_is_handed_back_for_repair(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(tool_calls(("ask_user", {"slot": "dates"})), ASK_DATES)

    events = await go(turn)

    failure = ToolFailure.model_validate_json(fake_llm.requests[1].messages[-1].content)
    assert "question" in failure.error
    assert stored(events) == [(MessageKind.EXPECTED_INPUT, "When are you going?")]


async def test_a_change_without_the_users_words_is_handed_back(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    change: dict[str, JsonValue] = {"type": "set_travel_mode", "no_flights": True}
    fake_llm.queue(
        tool_calls(("apply_user_changes", {"quote": "skip flights", "changes": [change]})),
        text("I can suggest that instead."),
    )

    events = await say(turn, "What's Lisbon like in May?")

    failure = ToolFailure.model_validate_json(fake_llm.requests[1].messages[-1].content)
    assert "use suggest_changes" in failure.error
    assert stored(events) == [(MessageKind.MESSAGE, "I can suggest that instead.")]
    assert not (await plan_of(turn)).no_flights


async def test_the_last_round_only_offers_what_ends_the_turn(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(*[VALIDATE] * MAX_ROUNDS)

    events = await go(turn)

    assert len(fake_llm.requests) == MAX_ROUNDS
    assert tools_of(fake_llm.requests[-2])[0] == "validate_plan"
    assert tools_of(fake_llm.requests[-1]) == ENDING
    # The model never got to the user, so a fixed message says what is left.
    assert stored(events) == [
        (
            MessageKind.NUDGE,
            "The trip still needs: a flight out; a flight home; a place to stay May 1 to "
            "May 8; at least one activity. Tell me which to work on.",
        )
    ]


async def test_an_empty_reply_falls_back_to_what_is_left(
    turn: TurnContext, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(text("  "))

    events = await go(turn)

    kind, content = stored(events)[0]
    assert kind == MessageKind.NUDGE and content.startswith("The trip still needs: a flight out")


async def test_a_model_failure_is_the_turns_to_report(turn: TurnContext, fake_llm: FakeLLM) -> None:
    fake_llm.queue(LLMError("The model is busy."))

    with pytest.raises(LLMError, match="busy"):
        await go(turn)
