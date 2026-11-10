"""Picks: checking candidate ids against an offer."""

from uuid import uuid4

import pytest

from features.planning.picks import PickError, describe_pick, picked_candidates
from features.planning.schemas import Candidate, CandidateChange, OfferChoice
from features.plans.schemas import SlotName


def _candidate(number: int) -> Candidate:
    change = CandidateChange(name="add_activity", arguments={}, summary=f"Add {number}")
    return Candidate(id=str(number), title=f"Option {number}", changes=[change])


def _offer(min_picks: int = 1, max_picks: int = 1) -> OfferChoice:
    return OfferChoice(
        offer_id=uuid4(),
        slot=SlotName.FLIGHT_OUTBOUND,
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
