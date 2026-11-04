"""The planning rules engine: which action each plan state leads to (pure, no database)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from features.itinerary.schemas import ItineraryItem, TimelineWarning
from features.planning.engine import (
    evaluate,
    next_action,
    night_ranges,
    offer_fits,
    refresh_search,
)
from features.planning.schemas import (
    AdvanceStage,
    AskInput,
    Candidate,
    CandidateChange,
    Complete,
    NextAction,
    NightRange,
    Offer,
    OfferChoice,
    OfferStatus,
    RunSearch,
)
from features.plans.schemas import IssueCode, PlanSnapshot, PlanStage, SlotName
from tests.features.itinerary.factories import PLAN_ID, activity, lodging
from tests.features.plans.builders import (
    CREATED,
    complete_trip,
    outbound_flight,
    return_flight,
    snapshot,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

FLIGHT_PREFS: dict[str, Any] = {"flights": {}}
LODGING_PREFS: dict[str, Any] = {"lodging": {"hotel_class": "midrange"}}
ACTIVITY_PREFS: dict[str, Any] = {"activities": {"interests": ["food"]}}
ALL_PREFS: dict[str, Any] = {**FLIGHT_PREFS, **LODGING_PREFS, **ACTIVITY_PREFS}

MAY_1_TO_4 = NightRange(check_in=date(2027, 5, 1), check_out=date(2027, 5, 4))


def _offer(
    slot: SlotName,
    stage: PlanStage,
    *,
    version: int = 1,
    status: OfferStatus = "open",
    nights: NightRange | None = None,
    gap: Any = None,
) -> Offer:
    candidate = Candidate(
        id="c1",
        title="Option one",
        changes=[CandidateChange(name="add_item", arguments={}, summary="Add option one")],
    )
    return Offer(
        id=uuid4(),
        plan_id=PLAN_ID,
        slot=slot,
        stage=stage,
        plan_version=version,
        candidates=[candidate],
        status=status,
        query_hash="hash",
        gap=gap,
        nights=nights,
        created_at=CREATED,
        decided_at=None,
    )


def _summary(action: NextAction) -> tuple[str, ...]:
    """The part of an action a case checks: its type and what it is about."""
    match action:
        case AskInput():
            return ("ask", action.slot)
        case OfferChoice():
            return ("offer", action.slot)
        case RunSearch():
            return ("search", action.kind)
        case AdvanceStage():
            return ("advance", action.to)
        case Complete():
            return ("complete",)


# ---------------------------------------------------------------------------
# Next action, one case per rule
# ---------------------------------------------------------------------------

OfferFor = Callable[[PlanSnapshot], Offer | None]


@dataclass(frozen=True)
class Case:
    name: str
    expected: tuple[str, ...]
    items: Sequence[ItineraryItem] = ()
    plan: dict[str, Any] = field(default_factory=dict)
    offer: OfferFor = lambda _plan: None


def _in(stage: PlanStage, **plan: Any) -> dict[str, Any]:
    return {"stage": stage, **plan}


def _stay(start: str, end: str) -> ItineraryItem:
    return lodging(start, end)


CASES: list[Case] = [
    # ----- basics -----
    Case(
        "a new plan asks where",
        ("ask", SlotName.DESTINATION),
        plan={"destination": None, "start_date": None, "end_date": None, "travelers": None},
    ),
    Case("no dates asks dates", ("ask", SlotName.DATES), plan={"end_date": None}),
    Case("no travelers asks who", ("ask", SlotName.TRAVELERS), plan={"travelers": None}),
    Case("no origin asks where from", ("ask", SlotName.ORIGIN), plan={"origin": None}),
    Case("basics done asks the optional budget", ("ask", SlotName.BUDGET)),
    Case(
        "a declined budget advances",
        ("advance", PlanStage.FLIGHTS),
        plan={"preferences": {"declined": ["budget"]}},
    ),
    Case(
        "a budget advances",
        ("advance", PlanStage.FLIGHTS),
        plan={"budget": {"amount": 3000}},
    ),
    # ----- flights -----
    Case(
        "flights ask preferences first",
        ("ask", SlotName.FLIGHT_PREFERENCES),
        plan=_in(PlanStage.FLIGHTS),
    ),
    Case(
        "flights with preferences search out",
        ("search", "flights_outbound"),
        plan=_in(PlanStage.FLIGHTS, preferences=FLIGHT_PREFS),
    ),
    Case(
        "a matching offer is shown",
        ("offer", SlotName.FLIGHT_OUTBOUND),
        plan=_in(PlanStage.FLIGHTS, preferences=FLIGHT_PREFS),
        offer=lambda _: _offer(SlotName.FLIGHT_OUTBOUND, PlanStage.FLIGHTS),
    ),
    Case(
        "an offer from an older plan version searches again",
        ("search", "flights_outbound"),
        plan=_in(PlanStage.FLIGHTS, preferences=FLIGHT_PREFS, version=2),
        offer=lambda _: _offer(SlotName.FLIGHT_OUTBOUND, PlanStage.FLIGHTS, version=1),
    ),
    Case(
        "an offer for another slot is ignored",
        ("search", "flights_outbound"),
        plan=_in(PlanStage.FLIGHTS, preferences=FLIGHT_PREFS),
        offer=lambda _: _offer(SlotName.FLIGHT_RETURN, PlanStage.FLIGHTS),
    ),
    Case(
        "a picked offer is ignored",
        ("search", "flights_outbound"),
        plan=_in(PlanStage.FLIGHTS, preferences=FLIGHT_PREFS),
        offer=lambda _: _offer(SlotName.FLIGHT_OUTBOUND, PlanStage.FLIGHTS, status="picked"),
    ),
    Case(
        "an outbound flight searches home",
        ("search", "flights_return"),
        items=[outbound_flight()],
        plan=_in(PlanStage.FLIGHTS, preferences=FLIGHT_PREFS),
    ),
    Case(
        "an outbound flight shows the return offer",
        ("offer", SlotName.FLIGHT_RETURN),
        items=[outbound_flight()],
        plan=_in(PlanStage.FLIGHTS, preferences=FLIGHT_PREFS),
        offer=lambda _: _offer(SlotName.FLIGHT_RETURN, PlanStage.FLIGHTS),
    ),
    Case(
        "both flights advance even without preferences",
        ("advance", PlanStage.LODGING),
        items=[outbound_flight(), return_flight()],
        plan=_in(PlanStage.FLIGHTS),
    ),
    Case(
        "skipped flights advance",
        ("advance", PlanStage.LODGING),
        plan=_in(PlanStage.FLIGHTS, flights_skipped=True),
    ),
    Case(
        "a later stage re-asks a missing basic",
        ("ask", SlotName.DESTINATION),
        plan=_in(PlanStage.FLIGHTS, destination=None, preferences=FLIGHT_PREFS),
    ),
    Case(
        "a later stage doesn't re-ask the optional budget",
        ("search", "flights_outbound"),
        plan=_in(PlanStage.FLIGHTS, budget=None, preferences=FLIGHT_PREFS),
    ),
    # ----- lodging -----
    Case(
        "lodging asks preferences first",
        ("ask", SlotName.LODGING_PREFERENCES),
        plan=_in(PlanStage.LODGING),
    ),
    Case(
        "lodging with preferences searches",
        ("search", "lodging"),
        plan=_in(PlanStage.LODGING, preferences=LODGING_PREFS),
    ),
    Case(
        "an offer for the same nights is shown",
        ("offer", SlotName.LODGING_STAY),
        plan=_in(PlanStage.LODGING, preferences=LODGING_PREFS),
        offer=lambda _: _offer(SlotName.LODGING_STAY, PlanStage.LODGING, nights=MAY_1_TO_4),
    ),
    Case(
        "an offer for other nights searches again",
        ("search", "lodging"),
        items=[_stay("2027-05-01T15:00", "2027-05-02T11:00")],
        plan=_in(PlanStage.LODGING, preferences=LODGING_PREFS),
        offer=lambda _: _offer(SlotName.LODGING_STAY, PlanStage.LODGING, nights=MAY_1_TO_4),
    ),
    Case(
        "every night covered advances",
        ("advance", PlanStage.ACTIVITIES),
        items=[_stay("2027-05-01T15:00", "2027-05-04T11:00")],
        plan=_in(PlanStage.LODGING),
    ),
    # ----- activities -----
    Case(
        "activities ask preferences first",
        ("ask", SlotName.ACTIVITY_PREFERENCES),
        plan=_in(PlanStage.ACTIVITIES),
    ),
    Case(
        "activities with preferences search",
        ("search", "activities"),
        plan=_in(PlanStage.ACTIVITIES, preferences=ACTIVITY_PREFS),
    ),
    Case(
        "an activity advances",
        ("advance", PlanStage.GROUND),
        items=[activity("2027-05-02T10:00", "2027-05-02T11:00")],
        plan=_in(PlanStage.ACTIVITIES),
    ),
    # ----- ground -----
    Case(
        "a gap searches for a transfer",
        ("search", "transfers"),
        items=[_stay("2027-05-01T15:00", "2027-05-04T11:00")],
        plan=_in(PlanStage.GROUND),
    ),
    Case(
        "an offer for the first gap is shown",
        ("offer", SlotName.TRANSFER),
        items=[_stay("2027-05-01T15:00", "2027-05-04T11:00")],
        plan=_in(PlanStage.GROUND),
        offer=lambda plan: _offer(SlotName.TRANSFER, PlanStage.GROUND, gap=plan.gaps[0]),
    ),
    Case(
        "an offer for a later gap searches for the first",
        ("search", "transfers"),
        items=[_stay("2027-05-01T15:00", "2027-05-04T11:00")],
        plan=_in(PlanStage.GROUND),
        offer=lambda plan: _offer(SlotName.TRANSFER, PlanStage.GROUND, gap=plan.gaps[1]),
    ),
    Case(
        "no gaps advance to review",
        ("advance", PlanStage.REVIEW),
        items=complete_trip(),
        plan=_in(PlanStage.GROUND),
    ),
    # ----- review -----
    Case("review is complete", ("complete",), plan=_in(PlanStage.REVIEW)),
    Case(
        "review is complete even with gaps",
        ("complete",),
        items=[_stay("2027-05-01T15:00", "2027-05-04T11:00")],
        plan=_in(PlanStage.REVIEW, preferences=ALL_PREFS),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_next_action(case: Case) -> None:
    plan = snapshot(case.items, **case.plan)

    assert _summary(next_action(plan, case.offer(plan))) == case.expected


# ---------------------------------------------------------------------------
# Action details
# ---------------------------------------------------------------------------


def test_ask_input_carries_the_slot_form() -> None:
    action = next_action(snapshot(travelers=None), None)

    assert isinstance(action, AskInput)
    assert action.stage == PlanStage.BASICS
    assert action.prompt == "Who's coming?"
    assert [f.name for f in action.fields] == ["adults", "children"]
    assert action.optional is False


def test_ask_input_prefills_what_the_plan_already_has() -> None:
    action = next_action(snapshot(end_date=None), None)

    assert isinstance(action, AskInput)
    assert action.prefill == {"dates": {"start": "2027-05-01"}}


def test_the_budget_is_optional() -> None:
    action = next_action(snapshot(), None)

    assert isinstance(action, AskInput)
    assert action.optional is True


def test_an_offer_choice_lists_the_offer() -> None:
    plan = snapshot(stage=PlanStage.ACTIVITIES, preferences=ACTIVITY_PREFS)
    offer = _offer(SlotName.ACTIVITIES, PlanStage.ACTIVITIES)

    action = next_action(plan, offer)

    assert isinstance(action, OfferChoice)
    assert action.offer_id == offer.id
    assert [c.id for c in action.candidates] == ["c1"]
    # Activities are a multi-pick; everything else picks one.
    assert (action.min_picks, action.max_picks) == (1, 8)


def test_a_lodging_search_covers_the_first_run_of_uncovered_nights() -> None:
    # Night of May 2 covered: May 1 and May 3 are separate stays.
    stay = lodging("2027-05-02T15:00", "2027-05-03T11:00")
    plan = snapshot([stay], stage=PlanStage.LODGING, preferences=LODGING_PREFS)

    action = next_action(plan, None)

    assert isinstance(action, RunSearch)
    assert action.nights == NightRange(check_in=date(2027, 5, 1), check_out=date(2027, 5, 2))
    assert action.label == "Find a place to stay the night of May 1"


def test_a_transfer_search_names_both_ends() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-04T11:00")
    plan = snapshot([stay], stage=PlanStage.GROUND)

    action = next_action(plan, None)

    assert isinstance(action, RunSearch)
    assert action.gap == plan.gaps[0]
    assert action.label == "Find a way from Home (San Francisco) to Hotel Okura"


def test_searched_choices_can_be_refreshed_but_transfers_cannot() -> None:
    activities = snapshot(stage=PlanStage.ACTIVITIES, preferences=ACTIVITY_PREFS)
    ground = snapshot([lodging("2027-05-01T15:00", "2027-05-04T11:00")], stage=PlanStage.GROUND)

    picked = next_action(activities, _offer(SlotName.ACTIVITIES, PlanStage.ACTIVITIES))
    transfer = next_action(ground, _offer(SlotName.TRANSFER, PlanStage.GROUND, gap=ground.gaps[0]))

    assert isinstance(picked, OfferChoice) and picked.can_refresh is True
    # Transfers come from rules that list every way across at once: nothing more to find.
    assert isinstance(transfer, OfferChoice) and transfer.can_refresh is False


def test_refresh_search_is_the_search_behind_the_choices() -> None:
    plan = snapshot(stage=PlanStage.ACTIVITIES, preferences=ACTIVITY_PREFS)

    action = refresh_search(plan)

    assert action == next_action(plan, None)
    assert isinstance(action, RunSearch) and action.kind == "activities"


def test_refresh_search_is_none_without_searched_choices() -> None:
    asking = snapshot(stage=PlanStage.ACTIVITIES)  # no preferences yet: a form
    ground = snapshot([lodging("2027-05-01T15:00", "2027-05-04T11:00")], stage=PlanStage.GROUND)

    assert refresh_search(asking) is None
    assert refresh_search(ground) is None


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def test_can_advance_follows_the_current_checklist() -> None:
    assert evaluate(snapshot(), None, []).can_advance is True
    assert evaluate(snapshot(stage=PlanStage.FLIGHTS), None, []).can_advance is False
    # Review is the last stage: nowhere to advance to.
    review = snapshot(complete_trip(), stage=PlanStage.REVIEW)
    assert evaluate(review, None, []).can_advance is False


def test_issues_cover_the_stages_reached_so_far() -> None:
    plan = snapshot(stage=PlanStage.FLIGHTS)

    issues = evaluate(plan, None, []).issues

    # Lodging, activities and ground aren't reached yet, so their checklists don't show.
    assert {issue.stage for issue in issues} == {PlanStage.FLIGHTS}


def test_review_lists_earlier_stages_issues_not_its_own_summary() -> None:
    plan = snapshot(stage=PlanStage.REVIEW)

    codes = {issue.code for issue in evaluate(plan, None, []).issues}

    assert IssueCode.STAGE_INCOMPLETE not in codes
    assert IssueCode.MISSING_OUTBOUND in codes


def test_timeline_warnings_become_warning_issues_on_their_items_stage() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-04T11:00")
    plan = snapshot([stay], stage=PlanStage.LODGING)
    warnings = [
        TimelineWarning(kind="missing_cost", message="Hotel Okura has no cost", item_ids=[stay.id]),
        TimelineWarning(kind="over_budget", message="The trip is over budget"),
        # Already a blocking checklist issue; not repeated as a warning.
        TimelineWarning(kind="no_lodging", message="No lodging for the night of May 1"),
    ]

    issues = [i for i in evaluate(plan, None, warnings).issues if i.severity == "warning"]

    assert [(i.code, i.stage) for i in issues] == [
        (IssueCode.MISSING_COST, PlanStage.LODGING),
        (IssueCode.OVER_BUDGET, PlanStage.REVIEW),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_night_ranges_join_consecutive_nights() -> None:
    nights = [date(2027, 5, 3), date(2027, 5, 1), date(2027, 5, 2), date(2027, 5, 5)]

    assert night_ranges(nights) == [
        NightRange(check_in=date(2027, 5, 1), check_out=date(2027, 5, 4)),
        NightRange(check_in=date(2027, 5, 5), check_out=date(2027, 5, 6)),
    ]
    assert night_ranges([]) == []


def test_offer_fits_needs_the_same_stage() -> None:
    plan = snapshot(stage=PlanStage.FLIGHTS)
    offer = _offer(SlotName.FLIGHT_OUTBOUND, PlanStage.LODGING)

    assert offer_fits(offer, plan, SlotName.FLIGHT_OUTBOUND) is False
