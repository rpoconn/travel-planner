"""What the plan is waiting on the user for: the newest of the form last asked, the open
offer and the pending proposal, leaving out what was made for an earlier plan."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from features.planning.pending import AskedForm, offer_choice, pending_interaction
from features.planning.schemas import (
    AskInput,
    Candidate,
    CandidateChange,
    NightRange,
    Offer,
    OfferChoice,
    Proposal,
    ProposalChange,
    RemoveItem,
    ReviewProposal,
)
from features.planning.slots import SLOTS
from features.plans.schemas import PlanStage, SlotName
from tests.features.plans.builders import snapshot

T0 = datetime(2026, 11, 10, 12, tzinfo=UTC)
PLAN = snapshot(version=7)

CANDIDATE = Candidate(
    id="1",
    title="Hotel Okura",
    changes=[CandidateChange(name="add_lodging", arguments={}, summary="Add Hotel Okura")],
)


def _form(minutes: int = 0) -> AskedForm:
    spec = SLOTS[SlotName.BUDGET]
    form = AskInput(
        slot=spec.name,
        stage=spec.stage,
        prompt=spec.prompt,
        fields=list(spec.fields),
        optional=spec.optional,
    )
    return AskedForm(form=form, at=T0 + timedelta(minutes=minutes))


def _offer(minutes: int = 0, *, version: int = 7, status: str = "open") -> Offer:
    return Offer.model_validate(
        {
            "id": uuid4(),
            "plan_id": PLAN.id,
            "slot": SlotName.LODGING_STAY,
            "stage": PlanStage.LODGING,
            "plan_version": version,
            "candidates": [CANDIDATE],
            "status": status,
            "query_hash": "q",
            "gap": None,
            "nights": NightRange(check_in=T0.date(), check_out=T0.date() + timedelta(days=3)),
            "created_at": T0 + timedelta(minutes=minutes),
            "decided_at": None,
        }
    )


def _proposal(minutes: int = 0, *, version: int = 7, status: str = "pending") -> Proposal:
    change = ProposalChange(summary="Remove “Dinner”", command=RemoveItem(item=2))
    return Proposal.model_validate(
        {
            "id": uuid4(),
            "plan_id": PLAN.id,
            "status": status,
            "reason": "It overlaps the flight.",
            "changes": [change],
            "plan_version": version,
            "created_at": T0 + timedelta(minutes=minutes),
            "decided_at": None,
        }
    )


def test_nothing_asked_is_nothing_pending() -> None:
    assert pending_interaction(PLAN, asked=None, offer=None, proposal=None) is None


def test_an_offer_is_shown_as_its_slots_choices() -> None:
    offer = _offer()

    choice = offer_choice(offer)

    assert choice == OfferChoice(
        offer_id=offer.id,
        slot=SlotName.LODGING_STAY,
        stage=PlanStage.LODGING,
        prompt="Pick a place to stay.",
        candidates=[CANDIDATE],
        nights=offer.nights,
        can_refresh=True,
    )
    assert pending_interaction(PLAN, asked=None, offer=offer, proposal=None) == choice


def test_the_newest_of_the_three_is_pending() -> None:
    form, offer, proposal = _form(1), _offer(2), _proposal(3)

    newest = pending_interaction(PLAN, asked=form, offer=offer, proposal=proposal)
    without = pending_interaction(PLAN, asked=form, offer=offer, proposal=None)
    asked_last = pending_interaction(PLAN, asked=_form(5), offer=offer, proposal=proposal)

    assert isinstance(newest, ReviewProposal) and newest.proposal_id == proposal.id
    assert isinstance(without, OfferChoice)
    assert asked_last == form.form


def test_an_offer_or_proposal_for_an_earlier_plan_is_not_pending() -> None:
    form = _form()

    pending = pending_interaction(
        PLAN, asked=form, offer=_offer(1, version=6), proposal=_proposal(2, version=6)
    )

    assert pending == form.form


def test_a_closed_offer_or_decided_proposal_is_not_pending() -> None:
    pending = pending_interaction(
        PLAN, asked=None, offer=_offer(status="picked"), proposal=_proposal(status="rejected")
    )

    assert pending is None
