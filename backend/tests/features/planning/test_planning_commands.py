"""Edits typed as free text, over HTTP: the reader hands an edit to the command reader, the
commands are checked and written, the transcript says what changed, and the engine drives
on from the changed plan (back to a stage the change broke, or the pending question).

The plan is the Lisbon trip without flights (`planning_http.reach`): its items, in time
order, are 1 "Friend's flat" (May 1-8) and 2 "Dinner in" (May 2). The model is scripted.
"""

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue

from features.itinerary.schemas import ItineraryItem
from features.planning.offline import planning_reply
from features.planning.schemas import ChangesRecord, RejectedChange, TravelPlan
from features.plans.schemas import PlanStage
from shared.llm.fake import FakeLLM, json_reply
from tests.features.planning.planning_http import (
    BASICS,
    asking,
    create_plan,
    done,
    messages,
    reach,
    say,
    stored,
)

FLAT_REF, DINNER_REF = 1, 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _plan_at(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], stage: PlanStage
) -> str:
    plan_id = await create_plan(client, headers, **BASICS)
    await reach(app, client, headers, plan_id, stage)
    return plan_id


def _commands(*commands: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"status": "command", "commands": list(commands)}


def _remove(ref: int) -> dict[str, JsonValue]:
    return {"type": "remove_item", "item": ref}


def _flat_end(end: str) -> dict[str, JsonValue]:
    return {"type": "update_item", "item": FLAT_REF, "changes": {"end": end}}


async def _items(client: AsyncClient, headers: dict[str, str], plan_id: str) -> list[ItineraryItem]:
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    return TravelPlan.model_validate_json(response.content).items


# ---------------------------------------------------------------------------
# Nothing pending
# ---------------------------------------------------------------------------


async def test_removing_the_only_stay_goes_back_to_lodging(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    # At the ground stage a transfer search is next: nothing to answer, so the command
    # reader reads the text first.
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)
    fake_llm.queue(json_reply(_commands(_remove(FLAT_REF))))

    events = await say(client, headers, plan_id, "remove the flat")

    assert stored(events) == [
        ("message", "remove the flat"),
        ("changes", "Removed “Friend's flat”."),
        ("stage", "Back to lodging: the trip still needs a place to stay the night of May 1."),
        ("expected_input", "What kind of place do you want to stay in?"),
    ]
    assert done(events).plan.stage == PlanStage.LODGING
    changes = (await messages(client, headers, plan_id))[1]
    assert isinstance(changes.payload, ChangesRecord)
    assert [change.name for change in changes.payload.applied] == ["remove_item"]
    # Cancelled, not deleted.
    statuses = [item.status for item in await _items(client, headers, plan_id)]
    assert statuses == ["cancelled", "proposed"]
    # The prompt showed the numbered itinerary.
    (request,) = fake_llm.requests
    assert "1. lodging “Friend's flat”" in request.messages[0].content


async def test_going_back_to_a_finished_stage_waits_there(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)
    go_to: dict[str, JsonValue] = {"type": "change_stage", "action": "go_to", "stage": "lodging"}
    fake_llm.queue(json_reply(_commands(go_to)))

    events = await say(client, headers, plan_id, "go back to lodging")

    # Lodging is done, so the engine would advance again at once; the turn stops instead
    # and leaves Continue to the user.
    assert stored(events)[1:] == [("changes", "Back to lodging.")]
    plan = done(events).plan
    assert plan.stage == PlanStage.LODGING
    assert plan.assessment.next_action.type == "advance_stage"


async def test_a_question_is_still_answered(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)
    fake_llm.queue(json_reply({"status": "question", "commands": []}), json_reply("Yes."))

    events = await say(client, headers, plan_id, "Is the flat near a tram?")

    assert stored(events)[1:] == [("message", "Yes.")]
    assert len(await _items(client, headers, plan_id)) == 2


# ---------------------------------------------------------------------------
# A form pending
# ---------------------------------------------------------------------------


async def test_an_edit_while_a_form_is_pending_is_written_then_the_form_returns(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.ACTIVITIES)
    fake_llm.queue(
        json_reply({"status": "command", "value": None}),  # the extractor hands it on
        json_reply(_commands(_flat_end("2027-05-08T09:00"))),
    )

    events = await say(client, headers, plan_id, "check out of the flat at 9")

    assert stored(events)[1:] == [
        ("changes", "Changed “Friend's flat”: end."),
        ("expected_input", "What do you like to do?"),
    ]
    assert asking(events) == "activity_preferences"
    (flat,) = await _items(client, headers, plan_id)
    assert flat.end.isoformat() == "2027-05-08T09:00:00+01:00"
    _extractor, reader = fake_llm.requests
    assert 'waiting for an answer to: "What do you like to do?"' in (reader.messages[0].content)


async def test_a_broken_rule_goes_back_to_the_reader_once(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.ACTIVITIES)
    fake_llm.queue(
        json_reply({"status": "command", "value": None}),
        json_reply(_commands(_flat_end("2027-04-30T09:00"))),  # before check-in
        json_reply(_commands(_flat_end("2027-05-08T09:00"))),  # repaired
    )

    events = await say(client, headers, plan_id, "check out of the flat at 9")

    assert stored(events)[1] == ("changes", "Changed “Friend's flat”: end.")
    repair = fake_llm.requests[-1]
    assert [m.role for m in repair.messages[-3:]] == ["user", "assistant", "user"]
    assert "2027-04-30T09:00" in repair.messages[-2].content
    reasons = repair.messages[-1].content
    assert "Change “Friend's flat”: lodging: end must be at or after start" in reasons


async def test_when_the_repair_breaks_a_rule_too_nothing_is_written(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.ACTIVITIES)
    before = await _items(client, headers, plan_id)
    broken = json_reply(_commands(_flat_end("2027-04-30T09:00")))
    fake_llm.queue(json_reply({"status": "command", "value": None}), broken, broken)

    events = await say(client, headers, plan_id, "check out of the flat on April 30")

    kind, content = stored(events)[1]
    assert kind == "changes"
    assert content.startswith("Couldn't change “Friend's flat”: lodging: end must be")
    # The form comes back, and the plan is as it was.
    assert stored(events)[2] == ("expected_input", "What do you like to do?")
    assert await _items(client, headers, plan_id) == before
    record = (await messages(client, headers, plan_id))[1].payload
    assert isinstance(record, ChangesRecord) and record.applied == []
    assert [type(change) for change in record.rejected] == [RejectedChange]


async def test_an_edit_the_reader_turns_down_gets_the_nudge(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.ACTIVITIES)
    fake_llm.queue(
        json_reply({"status": "command", "value": None}),
        json_reply({"status": "unrelated", "commands": []}),
    )

    events = await say(client, headers, plan_id, "add a museum visit on Friday")

    ((kind, _nudge),) = stored(events)[1:]
    assert kind == "nudge"


# ---------------------------------------------------------------------------
# Asking which
# ---------------------------------------------------------------------------


async def test_an_ambiguous_edit_asks_which_and_reads_the_reply_with_it(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)
    which = "Which one: the flat or the dinner?"
    fake_llm.queue(json_reply({"status": "ambiguous", "clarification": which}))

    asked = await say(client, headers, plan_id, "remove it")

    # Nothing is written and nothing runs: the reply comes first.
    assert stored(asked)[1:] == [("changes", which)]
    assert done(asked).plan.stage == PlanStage.GROUND

    fake_llm.queue(json_reply(_commands(_remove(DINNER_REF))))

    events = await say(client, headers, plan_id, "the dinner")

    assert stored(events)[1] == ("changes", "Removed “Dinner in”.")
    reader = fake_llm.requests[-1]
    assert [(m.role, m.content) for m in reader.messages[1:]] == [
        ("user", "remove it"),
        ("assistant", which),
        ("user", "the dinner"),
    ]


# ---------------------------------------------------------------------------
# Offline
# ---------------------------------------------------------------------------


async def test_the_offline_fake_removes_an_item_by_name(
    app: FastAPI, client: AsyncClient, headers: dict[str, str]
) -> None:
    # LLM_PROVIDER=fake, as the Playwright suite runs: the phrase table finds "the flat" in
    # the numbered itinerary the real prompt shows.
    app.state.llm = FakeLLM(fallback=planning_reply)
    plan_id = await _plan_at(app, client, headers, PlanStage.ACTIVITIES)

    events = await say(client, headers, plan_id, "remove the flat")

    assert stored(events)[1:3] == [
        ("changes", "Removed “Friend's flat”."),
        ("stage", "Back to lodging: the trip still needs a place to stay the night of May 1."),
    ]
