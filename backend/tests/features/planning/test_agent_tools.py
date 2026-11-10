"""The planner's tools: what each call does, and what the system refuses whatever the
model asks for.

The plan has every basic and nothing else (`conftest.turn`): Lisbon, May 1-8 2027, two
adults from Philadelphia. Its problems are a flight out, a flight home, a place to stay
and something to do.
"""

import json
from typing import get_args

from httpx import AsyncClient
from pydantic import JsonValue

from features.chat.schemas import MessageKind
from features.planning.agent.context import ChoicesView
from features.planning.agent.tool_models import (
    TOOLS,
    InputSlot,
    SearchResult,
    Shown,
    ToolFailure,
    ToolName,
    ValidationResult,
    WriteResult,
    tool_specs,
)
from features.planning.agent.tools import (
    MAX_PAID_SEARCHES,
    ToolOutcome,
    TurnContext,
    TurnState,
    available,
)
from features.planning.events import PlanningMessageEvent, PlanningPlanEvent
from features.planning.schemas import AskInput, OfferChoice, ReviewProposal
from features.planning.slots import SLOTS
from features.plans import service as plans_service
from features.plans.schemas import SlotName
from shared.flights.fake import FakeFlightProvider
from tests.features.planning.agent_helpers import call, plan_of, set_preferences, stored, touch
from tests.features.planning.planning_http import FLAT, LISBON_TZ, add_item

NIGHTS = "uncovered_night:2027-05-01"
DRIVING = "We're driving down, actually."
NO_FLIGHTS: dict[str, JsonValue] = {"type": "set_travel_mode", "no_flights": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(outcome: ToolOutcome) -> str:
    assert isinstance(outcome.result, ToolFailure), outcome.result
    assert not outcome.ends_turn and not outcome.events
    return outcome.result.error


def _found(outcome: ToolOutcome) -> SearchResult:
    assert isinstance(outcome.result, SearchResult), outcome.result
    return outcome.result


def _wrote(outcome: ToolOutcome) -> WriteResult:
    assert isinstance(outcome.result, WriteResult), outcome.result
    return outcome.result


async def _typed(ctx: TurnContext, said: str = DRIVING) -> TurnState:
    """A turn the user typed, with every preference answered."""
    await set_preferences(ctx)
    return TurnState(ctx=ctx, said=said)


async def _offered(state: TurnState, kind: str = "flight_out") -> OfferChoice:
    """Search and show the choices, as two calls of a turn would."""
    found = _found(await call(state, "search", kind=kind))
    await call(state, "present_choices", search=found.search)
    pending = (await plan_of(state.ctx)).pending
    assert isinstance(pending, OfferChoice)
    return pending


async def _suggested(state: TurnState) -> ReviewProposal:
    await call(state, "suggest_changes", reason="No need to fly.", changes=[NO_FLIGHTS])
    pending = (await plan_of(state.ctx)).pending
    assert isinstance(pending, ReviewProposal)
    return pending


# ---------------------------------------------------------------------------
# What the model is offered
# ---------------------------------------------------------------------------


def test_every_input_slot_can_be_asked() -> None:
    inputs = {spec.name.value for spec in SLOTS.values() if spec.kind == "input"}

    assert set(get_args(InputSlot)) == inputs


def test_the_specs_are_self_contained() -> None:
    # Models follow `$ref`s unreliably, and Claude's tool schemas take no `oneOf`.
    names: tuple[ToolName, ...] = get_args(ToolName)
    assert set(names) == set(TOOLS)
    for spec in tool_specs(names):
        schema = json.dumps(spec.parameters)
        assert "$ref" not in schema and "oneOf" not in schema, spec.name
        assert spec.parameters["type"] == "object"


async def test_a_turn_from_the_interface_offers_no_writing_tool(turn: TurnContext) -> None:
    plan = await plan_of(turn)

    assert available(TurnState(ctx=turn), plan, last_round=False) == [
        "validate_plan",
        "search",
        "ask_user",
        "present_choices",
        "suggest_changes",
    ]


async def test_a_typed_turn_offers_the_writing_tools_that_apply(turn: TurnContext) -> None:
    state = await _typed(turn)
    assert available(state, await plan_of(turn), last_round=False)[-1] == "apply_user_changes"

    await _offered(state)
    assert available(state, await plan_of(turn), last_round=False)[-1] == "pick_choice"

    await _suggested(state)
    assert available(state, await plan_of(turn), last_round=False)[-1] == "decide_proposal"


async def test_the_last_round_only_offers_what_ends_the_turn(turn: TurnContext) -> None:
    state = await _typed(turn)

    assert available(state, await plan_of(turn), last_round=True) == [
        "ask_user",
        "present_choices",
        "suggest_changes",
    ]


async def test_a_tool_that_is_not_offered_is_refused(turn: TurnContext) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)  # nothing typed

    error = _error(await call(state, "apply_user_changes", quote="x", changes=[NO_FLIGHTS]))

    assert "isn't available now" in error
    assert not (await plan_of(turn)).flights_skipped


async def test_an_unknown_tool_and_bad_arguments_are_refused(turn: TurnContext) -> None:
    state = TurnState(ctx=turn)

    assert "isn't available now" in _error(await call(state, "book_flight"))
    assert "kind" in _error(await call(state, "search", kind="boat"))
    assert "question" in _error(await call(state, "ask_user", slot="dates"))


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------


async def test_validate_plan_lists_the_problems(turn: TurnContext) -> None:
    outcome = await call(TurnState(ctx=turn), "validate_plan")

    assert isinstance(outcome.result, ValidationResult)
    assert [problem.id for problem in outcome.result.problems] == [
        "missing_outbound",
        "missing_return",
        NIGHTS,
        "no_activities",
    ]
    assert outcome.result.complete is False


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_a_search_returns_its_candidates_by_ref(turn: TurnContext) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)

    found = _found(await call(state, "search", kind="flight_out"))

    assert found.search == "a"
    assert [candidate.ref for candidate in found.candidates] == ["a1", "a2"]
    assert found.searches_left == MAX_PAID_SEARCHES - 1
    # Nothing is shown or added until the model says so.
    plan = await plan_of(turn)
    assert plan.pending is None and plan.items == []


async def test_a_search_waits_for_its_preferences(
    turn: TurnContext, fake_flights: FakeFlightProvider
) -> None:
    state = TurnState(ctx=turn)

    error = _error(await call(state, "search", kind="flight_out"))

    assert "ask_user with slot flight_preferences" in error
    assert fake_flights.searches == []
    assert state.searches_left == MAX_PAID_SEARCHES


async def test_paid_searches_are_capped_per_turn(
    turn: TurnContext, fake_flights: FakeFlightProvider
) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)
    _found(await call(state, "search", kind="flight_out"))
    _found(await call(state, "search", kind="activities"))

    error = _error(await call(state, "search", kind="flight_out"))

    assert "No searches are left" in error
    assert len(fake_flights.searches) == 1


async def test_a_stay_search_names_the_nights_it_is_for(turn: TurnContext) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)

    assert NIGHTS in _error(await call(state, "search", kind="stay"))

    found = _found(await call(state, "search", kind="stay", problem=NIGHTS))
    nights = state.searches[found.search].action.nights
    assert nights is not None
    assert (nights.check_in.isoformat(), nights.check_out.isoformat()) == (
        "2027-05-01",
        "2027-05-08",
    )


async def test_a_transfer_search_is_free(
    turn: TurnContext, client: AsyncClient, headers: dict[str, str]
) -> None:
    await add_item(
        client,
        headers,
        str(turn.plan_id),
        type="lodging",
        title="Friend's flat",
        start="2027-05-01T15:00",
        end="2027-05-08T11:00",
        timezone=LISBON_TZ,
        place=FLAT,
    )
    await plans_service.set_no_flights(turn.session, turn.user_id, turn.plan_id, True)
    gaps = [p.id for p in (await plan_of(turn)).problems if p.fix.slot == SlotName.TRANSFER]
    state = TurnState(ctx=turn, paid_searches=MAX_PAID_SEARCHES)

    found = _found(await call(state, "search", kind="transfer", problem=gaps[0]))

    assert found.candidates
    assert state.paid_searches == MAX_PAID_SEARCHES


async def test_more_leaves_out_what_the_search_already_found(turn: TurnContext) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)
    _found(await call(state, "search", kind="flight_out"))

    # The canned search only knows two flights out.
    error = _error(await call(state, "search", kind="flight_out", more=True))

    assert "all the options" in error


async def test_flights_are_only_searched_for_while_one_is_missing(turn: TurnContext) -> None:
    await set_preferences(turn)
    await plans_service.set_no_flights(turn.session, turn.user_id, turn.plan_id, True)

    error = _error(await call(TurnState(ctx=turn), "search", kind="flight_out"))

    assert "without flights" in error


# ---------------------------------------------------------------------------
# ask_user
# ---------------------------------------------------------------------------


async def test_asking_a_slot_shows_its_form(turn: TurnContext) -> None:
    outcome = await call(
        TurnState(ctx=turn), "ask_user", question="Still May 1 to 8?", slot="dates"
    )

    assert outcome.ends_turn and outcome.result == Shown(shown="form")
    assert stored(list(outcome.events)) == [(MessageKind.EXPECTED_INPUT, "Still May 1 to 8?")]
    pending = (await plan_of(turn)).pending
    assert isinstance(pending, AskInput)
    assert (pending.slot, pending.prompt) == (SlotName.DATES, "Still May 1 to 8?")
    # What the plan already holds is filled in.
    assert pending.prefill == {"dates": {"start": "2027-05-01", "end": "2027-05-08"}}


async def test_asking_without_a_slot_is_a_plain_question(turn: TurnContext) -> None:
    outcome = await call(TurnState(ctx=turn), "ask_user", question="Beach or city?")

    assert outcome.ends_turn and outcome.result == Shown(shown="question")
    assert stored(list(outcome.events)) == [(MessageKind.MESSAGE, "Beach or city?")]
    assert (await plan_of(turn)).pending is None


# ---------------------------------------------------------------------------
# present_choices
# ---------------------------------------------------------------------------


async def test_presenting_a_search_opens_its_choices(turn: TurnContext) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)
    found = _found(await call(state, "search", kind="flight_out"))

    outcome = await call(state, "present_choices", search=found.search)

    assert outcome.ends_turn and outcome.result == Shown(shown="choices")
    assert stored(list(outcome.events)) == [(MessageKind.CHOICES, "Pick your flight out.")]
    pending = (await plan_of(turn)).pending
    assert isinstance(pending, OfferChoice)
    assert pending.slot == SlotName.FLIGHT_OUTBOUND
    assert [c.title for c in pending.candidates] == [c.title for c in found.candidates]


async def test_only_this_turns_fresh_searches_can_be_presented(turn: TurnContext) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)

    assert "no search b" in _error(await call(state, "present_choices", search="b"))

    found = _found(await call(state, "search", kind="flight_out"))
    await touch(turn)
    assert "changed since" in _error(await call(state, "present_choices", search=found.search))
    assert (await plan_of(turn)).pending is None


# ---------------------------------------------------------------------------
# suggest_changes
# ---------------------------------------------------------------------------


async def test_a_suggestion_waits_for_approval(turn: TurnContext) -> None:
    await set_preferences(turn)
    state = TurnState(ctx=turn)
    found = _found(await call(state, "search", kind="activities"))
    first = found.candidates[0]

    outcome = await call(
        state,
        "suggest_changes",
        reason="A good first morning.",
        changes=[{"type": "add_candidate", "candidate": first.ref}],
    )

    assert outcome.ends_turn and outcome.result == Shown(shown="suggestion")
    assert stored(list(outcome.events)) == [(MessageKind.MESSAGE, "A good first morning.")]
    plan = await plan_of(turn)
    assert isinstance(plan.pending, ReviewProposal)
    assert [change.summary for change in plan.pending.changes] == [f"Add “{first.title}”"]
    assert plan.items == []  # nothing is written until it is approved


async def test_a_suggestion_that_breaks_a_rule_is_not_stored(turn: TurnContext) -> None:
    error = _error(
        await call(
            TurnState(ctx=turn),
            "suggest_changes",
            reason="Tidier.",
            changes=[{"type": "remove_item", "item": 4}],
        )
    )

    assert "no item 4" in error
    assert (await plan_of(turn)).pending is None


# ---------------------------------------------------------------------------
# apply_user_changes
# ---------------------------------------------------------------------------


async def test_changes_the_user_asked_for_are_written_at_once(turn: TurnContext) -> None:
    state = await _typed(turn)

    outcome = await call(
        state, "apply_user_changes", quote="we're  DRIVING down", changes=[NO_FLIGHTS]
    )

    result = _wrote(outcome)
    assert result.changed and result.summary == "The trip has no flights."
    # The plan comes back as the write left it.
    assert result.plan.trip.no_flights is True
    assert [problem.id for problem in result.plan.problems] == [NIGHTS, "no_activities"]
    assert not outcome.ends_turn
    message, updated = outcome.events
    assert isinstance(message, PlanningMessageEvent) and isinstance(updated, PlanningPlanEvent)
    assert message.message.kind == MessageKind.CHANGES
    assert updated.plan.flights_skipped is True


async def test_changes_need_the_users_own_words(turn: TurnContext) -> None:
    state = await _typed(turn)

    error = _error(
        await call(state, "apply_user_changes", quote="skip the flights", changes=[NO_FLIGHTS])
    )

    assert "use suggest_changes" in error
    assert not (await plan_of(turn)).flights_skipped


async def test_a_change_the_rules_turn_down_is_not_stored(turn: TurnContext) -> None:
    state = await _typed(turn, "Remove the fourth thing.")

    outcome = await call(
        state,
        "apply_user_changes",
        quote="remove the fourth thing",
        changes=[{"type": "remove_item", "item": 4}],
    )

    result = _wrote(outcome)
    assert not result.changed and "no item 4" in result.summary
    assert not outcome.events


# ---------------------------------------------------------------------------
# pick_choice
# ---------------------------------------------------------------------------


async def test_picking_in_words_adds_the_choice(turn: TurnContext) -> None:
    state = await _typed(turn, "the second one")
    offer = await _offered(state)
    second = offer.candidates[1]

    outcome = await call(state, "pick_choice", candidates=[second.id])

    result = _wrote(outcome)
    assert result.changed and result.summary == f"Got it: {second.title}."
    assert [item.type for item in result.plan.items] == ["flight"]
    assert stored(list(outcome.events)) == [(MessageKind.MESSAGE, f"Got it: {second.title}.")]
    # The offer is closed, and the flight out is no longer missing.
    assert not isinstance(result.plan.pending, ChoicesView)
    assert "missing_outbound" not in [problem.id for problem in result.plan.problems]


async def test_a_pick_outside_the_choices_is_refused(turn: TurnContext) -> None:
    state = await _typed(turn, "the ninth one")
    await _offered(state)

    assert "no option 9" in _error(await call(state, "pick_choice", candidates=["9"]))
    assert (await plan_of(turn)).items == []


# ---------------------------------------------------------------------------
# decide_proposal
# ---------------------------------------------------------------------------


async def test_approving_in_words_makes_the_suggested_changes(turn: TurnContext) -> None:
    state = await _typed(turn, "yes, do that")
    await _suggested(state)

    outcome = await call(state, "decide_proposal", decision="approve")

    result = _wrote(outcome)
    assert result.changed and result.plan.trip.no_flights is True
    assert stored(list(outcome.events)) == [(MessageKind.CHANGES, "The trip has no flights.")]
    assert (await plan_of(turn)).pending is None


async def test_rejecting_in_words_changes_nothing(turn: TurnContext) -> None:
    state = await _typed(turn, "no thanks")
    await _suggested(state)

    outcome = await call(state, "decide_proposal", decision="reject")

    result = _wrote(outcome)
    assert not result.changed and result.plan.trip.no_flights is False
    assert not outcome.events
    assert (await plan_of(turn)).pending is None
