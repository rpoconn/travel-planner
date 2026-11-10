"""What the plan is waiting on the user for: a form, choices to pick from, or a proposal
to decide.

Pure functions (no database, no model). Each of the three is put to the user by something
stored: the form by the last transcript message, the choices by the open offer, the
proposal by the pending proposal. (A plan with an empty transcript opens on a form nothing
stored: `planning.service` works out which.) An offer or proposal made for an earlier version of the
plan is stale and no longer waits. When more than one still stands, the newest is the one
the user was last asked.
"""

from dataclasses import dataclass
from datetime import datetime

from features.planning.schemas import (
    AskInput,
    Offer,
    OfferChoice,
    Pending,
    Proposal,
    ReviewProposal,
    SuggestedChange,
)
from features.planning.slots import SLOTS
from features.plans.schemas import PlanSnapshot, SlotName


@dataclass(frozen=True)
class AskedForm:
    """The form the transcript's last message showed, and when; for a plan with an empty
    transcript, the form it opens on, as of the plan's creation."""

    form: AskInput
    at: datetime


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def ask_input(name: SlotName, plan: PlanSnapshot, prompt: str | None = None) -> AskInput:
    """Input slot `name` as the form the user fills in, with what the plan already holds
    filled in. `prompt` is the question above it (the slot's own by default)."""
    spec = SLOTS[name]
    answer = spec.read(plan) if spec.read is not None else None
    return AskInput(
        slot=name,
        stage=spec.stage,
        prompt=prompt or spec.prompt,
        fields=list(spec.fields),
        optional=spec.optional,
        prefill=answer.model_dump(mode="json") if answer is not None else {},
    )


def offer_choice(offer: Offer) -> OfferChoice:
    """The offer as the choices the user picks from."""
    spec = SLOTS[offer.slot]
    return OfferChoice(
        offer_id=offer.id,
        slot=offer.slot,
        stage=spec.stage,
        prompt=spec.prompt,
        candidates=offer.candidates,
        min_picks=spec.min_picks,
        max_picks=spec.max_picks,
        gap=offer.gap,
        nights=offer.nights,
        can_refresh=spec.refreshable,
    )


def review_proposal(proposal: Proposal) -> ReviewProposal:
    """The proposal as the card the user approves or rejects."""
    return ReviewProposal(
        proposal_id=proposal.id,
        prompt=proposal.reason,
        changes=[
            SuggestedChange(
                name=change.command.type, summary=change.summary, candidate=change.candidate
            )
            for change in proposal.changes
        ],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def pending_interaction(
    plan: PlanSnapshot,
    *,
    asked: AskedForm | None,
    offer: Offer | None,
    proposal: Proposal | None,
) -> Pending | None:
    """The newest of: the form last asked, the plan's open offer, its pending proposal.
    The offer and the proposal only count while they are for this version of the plan."""
    waiting: list[tuple[datetime, Pending]] = []
    if asked is not None:
        waiting.append((asked.at, asked.form))
    if offer is not None and offer.status == "open" and offer.plan_version == plan.version:
        waiting.append((offer.created_at, offer_choice(offer)))
    if (
        proposal is not None
        and proposal.status == "pending"
        and proposal.plan_version == plan.version
    ):
        waiting.append((proposal.created_at, review_proposal(proposal)))
    if not waiting:
        return None
    return max(waiting, key=lambda entry: entry[0])[1]
