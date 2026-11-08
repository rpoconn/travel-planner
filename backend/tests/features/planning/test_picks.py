"""Picks: checking candidate ids against an offer, and reading a pick from free text with a
scripted model."""

from uuid import uuid4

import pytest

from features.planning.extractor import MAX_ATTEMPTS, PICK_SCHEMA, extract_pick
from features.planning.picks import PickError, describe_pick, picked_candidates
from features.planning.schemas import Candidate, CandidateChange, OfferChoice
from features.plans.schemas import PlanStage, SlotName
from shared.llm.fake import FakeLLM, json_reply
from tests.features.plans.builders import snapshot


def _candidate(number: int) -> Candidate:
    change = CandidateChange(name="add_activity", arguments={}, summary=f"Add {number}")
    return Candidate(id=str(number), title=f"Option {number}", changes=[change])


def _offer(min_picks: int = 1, max_picks: int = 1) -> OfferChoice:
    return OfferChoice(
        offer_id=uuid4(),
        slot=SlotName.FLIGHT_OUTBOUND,
        stage=PlanStage.FLIGHTS,
        prompt="Pick your flight out.",
        candidates=[_candidate(n) for n in (1, 2, 3)],
        min_picks=min_picks,
        max_picks=max_picks,
    )


# ---------------------------------------------------------------------------
# Checking a pick
# ---------------------------------------------------------------------------


def test_the_picked_candidates_come_back_in_the_order_given() -> None:
    picked = picked_candidates(_offer(1, 3), ["3", "1"])

    assert [c.id for c in picked] == ["3", "1"]
    assert describe_pick(picked) == "Option 3; Option 1"


@pytest.mark.parametrize(
    ("ids", "bounds", "error"),
    [
        (["7"], (1, 1), "There's no option 7 in this list"),
        (["1", "1"], (1, 3), "Pick each option only once"),
        (["1", "2"], (1, 1), "Pick 1 option"),
        (["1"], (2, 3), "Pick at least 2 options"),
        (["1", "2", "3"], (1, 2), "Pick at most 2 options"),
    ],
)
def test_a_pick_the_offer_does_not_allow(
    ids: list[str], bounds: tuple[int, int], error: str
) -> None:
    with pytest.raises(PickError, match=error):
        picked_candidates(_offer(*bounds), ids)


# ---------------------------------------------------------------------------
# Reading a pick from free text
# ---------------------------------------------------------------------------


async def test_a_pick_is_read_with_the_options_in_the_prompt() -> None:
    llm = FakeLLM([json_reply({"status": "answered", "candidate_ids": ["2"]})])

    pick = await extract_pick(llm, _offer(), "the second one", snapshot())

    assert pick.candidates is not None and [c.id for c in pick.candidates] == ["2"]
    (request,) = llm.requests
    assert request.format == PICK_SCHEMA
    assert request.think is False
    assert '- id "2" (option 2): Option 2' in request.messages[0].content


async def test_a_read_pick_the_offer_does_not_allow_is_explained() -> None:
    llm = FakeLLM([json_reply({"status": "answered", "candidate_ids": ["1", "2"]})])

    pick = await extract_pick(llm, _offer(), "both", snapshot())

    assert (pick.status, pick.candidates, pick.error) == ("answered", None, "Pick 1 option")


@pytest.mark.parametrize("status", ["command", "question", "unrelated"])
async def test_an_edit_a_question_or_unrelated_text_picks_nothing(status: str) -> None:
    llm = FakeLLM([json_reply({"status": status, "candidate_ids": []})])

    pick = await extract_pick(llm, _offer(), "hm", snapshot())

    assert (pick.status, pick.candidates, pick.error) == (status, None, None)


async def test_answered_without_ids_counts_as_unrelated() -> None:
    llm = FakeLLM([json_reply({"status": "answered", "candidate_ids": []})])

    assert (await extract_pick(llm, _offer(), "yes", snapshot())).status == "unrelated"


async def test_malformed_output_is_retried_then_fails_closed() -> None:
    llm = FakeLLM([json_reply({"nope": 1})] * MAX_ATTEMPTS)

    pick = await extract_pick(llm, _offer(), "the first", snapshot())

    assert pick.status == "unrelated" and pick.candidates is None
    assert len(llm.requests) == MAX_ATTEMPTS
