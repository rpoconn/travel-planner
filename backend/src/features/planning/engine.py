"""The planning rules engine: given the plan as it stands, what happens next.

Pure functions (no database, no model). Each stage has an ordered list of rules; the first
that fires decides the next action. When none fires the stage is done and the engine says
to advance. Every decision reads itinerary state and typed slot values, never text.

| Stage      | Rules, in order                                                          |
|------------|--------------------------------------------------------------------------|
| basics     | destination, dates, travelers, origin, budget (optional)                 |
| flights    | preferences, pick a flight out, pick a flight home (or skipped)          |
| lodging    | preferences, then pick a stay for each run of uncovered nights           |
| activities | preferences, pick activities                                             |
| ground     | pick a transfer for each gap                                             |
| review     | complete                                                                 |

A pick slot shows the open offer when it is for that slot, stage, plan version and
gap/nights; otherwise it asks for a search. Later stages first re-ask any basics they
depend on (a stage can't search for flights without dates).
"""

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

from features.itinerary.gaps import describe_place
from features.itinerary.schemas import FlightItem, Gap, ItemType, TimelineWarning
from features.planning.schemas import (
    AdvanceStage,
    AskInput,
    Complete,
    NextAction,
    NightRange,
    Offer,
    OfferChoice,
    PlanAssessment,
    RunSearch,
    SearchKind,
)
from features.planning.slots import SLOTS, SlotSpec
from features.plans.schemas import (
    STAGES,
    Issue,
    PlanSnapshot,
    PlanStage,
    SlotName,
)
from features.plans.stages import stage_status
from shared.domain import IssueCode

# A rule returns the action it wants, or None to let the next rule (or advancing) decide.
_StageRule = Callable[[PlanSnapshot, Offer | None], NextAction | None]

# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

# Timeline warnings that don't become warning issues: the lodging and ground checklists
# already report them as blocking issues.
_CHECKLIST_WARNINGS = frozenset({IssueCode.UNCOVERED_NIGHT, IssueCode.MISSING_TRANSFER})

# The stage whose items a warning is about, by the type of its first item.
_ITEM_STAGES: dict[ItemType, PlanStage] = {
    "flight": PlanStage.FLIGHTS,
    "lodging": PlanStage.LODGING,
    "activity": PlanStage.ACTIVITIES,
    "transfer": PlanStage.GROUND,
}


def _warning_issue(warning: TimelineWarning, plan: PlanSnapshot) -> Issue | None:
    if warning.kind in _CHECKLIST_WARNINGS:
        return None
    types = {item.id: item.type for item in plan.items}
    first = next((types[i] for i in warning.item_ids if i in types), None)
    # A warning about no item in particular (over the whole budget) is fixed at review.
    stage = _ITEM_STAGES[first] if first is not None else PlanStage.REVIEW
    return Issue(
        code=warning.kind,
        stage=stage,
        severity="warning",
        message=warning.message,
        date=warning.date,
        item_ids=warning.item_ids,
    )


def plan_issues(plan: PlanSnapshot, warnings: Sequence[TimelineWarning]) -> list[Issue]:
    """Blocking issues of every stage reached so far, then the timeline's warnings. Later
    stages' checklists aren't issues yet, and review's own checklist only repeats the
    stages before it."""
    reached = STAGES[: STAGES.index(plan.stage) + 1]
    blocking = [
        issue
        for status in plan.stages
        if status.stage in reached and status.stage != PlanStage.REVIEW
        for issue in status.issues
    ]
    flagged = [_warning_issue(warning, plan) for warning in warnings]
    return blocking + [issue for issue in flagged if issue is not None]


# ---------------------------------------------------------------------------
# Action builders
# ---------------------------------------------------------------------------


def _prefill(name: SlotName, plan: PlanSnapshot) -> dict[str, Any]:
    """What the plan already holds for a slot, in the shape its form sends back."""
    prefs = plan.preferences
    match name:
        case SlotName.DESTINATION if plan.destination is not None:
            return {"destination": plan.destination.model_dump(mode="json")}
        case SlotName.ORIGIN if plan.origin is not None:
            return {"origin": plan.origin.model_dump(mode="json")}
        case SlotName.DATES:
            known = {"start": plan.start_date, "end": plan.end_date}
            dates = {key: value.isoformat() for key, value in known.items() if value is not None}
            return {"dates": dates} if dates else {}
        case SlotName.TRAVELERS if plan.travelers is not None:
            return plan.travelers.model_dump(mode="json")
        case SlotName.BUDGET if plan.budget is not None:
            return {"budget": plan.budget.model_dump(mode="json")}
        case SlotName.FLIGHT_PREFERENCES if prefs.flights is not None:
            return prefs.flights.model_dump(mode="json", exclude={"outbound_token"})
        case SlotName.LODGING_PREFERENCES if prefs.lodging is not None:
            return prefs.lodging.model_dump(mode="json")
        case SlotName.ACTIVITY_PREFERENCES if prefs.activities is not None:
            return prefs.activities.model_dump(mode="json")
    return {}


def _ask(name: SlotName, plan: PlanSnapshot) -> AskInput:
    spec = SLOTS[name]
    return AskInput(
        slot=name,
        stage=spec.stage,
        prompt=spec.prompt,
        fields=list(spec.fields),
        optional=spec.optional,
        prefill=_prefill(name, plan),
    )


def offer_fits(
    offer: Offer,
    plan: PlanSnapshot,
    slot: SlotName,
    *,
    gap: Gap | None = None,
    nights: NightRange | None = None,
) -> bool:
    """Whether `offer` is still the right list for `slot`: anything that changed the plan
    since (a pick, an edit) makes it stale, and a search runs again."""
    return (
        offer.status == "open"
        and offer.slot == slot
        and offer.stage == plan.stage
        and offer.plan_version == plan.version
        and offer.gap == gap
        and offer.nights == nights
    )


def _pick_or_search(
    plan: PlanSnapshot,
    offer: Offer | None,
    slot: SlotName,
    kind: SearchKind,
    label: str,
    *,
    gap: Gap | None = None,
    nights: NightRange | None = None,
) -> OfferChoice | RunSearch:
    spec: SlotSpec = SLOTS[slot]
    if offer is not None and offer_fits(offer, plan, slot, gap=gap, nights=nights):
        return OfferChoice(
            offer_id=offer.id,
            slot=slot,
            stage=spec.stage,
            prompt=spec.prompt,
            candidates=offer.candidates,
            min_picks=spec.min_picks,
            max_picks=spec.max_picks,
            gap=gap,
            nights=nights,
            can_refresh=kind != "transfers",
        )
    return RunSearch(kind=kind, slot=slot, stage=spec.stage, label=label, gap=gap, nights=nights)


# ---------------------------------------------------------------------------
# Stage rules
# ---------------------------------------------------------------------------


def _missing_basic(plan: PlanSnapshot, *, with_optional: bool) -> SlotName | None:
    """The first basics slot the plan lacks; the optional budget only when asked for."""
    if plan.destination is None:
        return SlotName.DESTINATION
    if plan.start_date is None or plan.end_date is None:
        return SlotName.DATES
    if plan.travelers is None:
        return SlotName.TRAVELERS
    if plan.origin is None:
        return SlotName.ORIGIN
    declined = plan.preferences.declined
    if with_optional and plan.budget is None and SlotName.BUDGET not in declined:
        return SlotName.BUDGET
    return None


def _basics(plan: PlanSnapshot, offer: Offer | None) -> NextAction | None:
    missing = _missing_basic(plan, with_optional=True)
    return _ask(missing, plan) if missing is not None else None


def _flights(plan: PlanSnapshot, offer: Offer | None) -> NextAction | None:
    if plan.preferences.flights is None:
        return _ask(SlotName.FLIGHT_PREFERENCES, plan)
    directions = {
        item.direction
        for item in plan.items
        if isinstance(item, FlightItem) and item.status != "cancelled"
    }
    if "outbound" not in directions:
        return _pick_or_search(
            plan, offer, SlotName.FLIGHT_OUTBOUND, "flights_outbound", "Find flights out"
        )
    return _pick_or_search(
        plan, offer, SlotName.FLIGHT_RETURN, "flights_return", "Find flights home"
    )


def night_ranges(nights: Sequence[date]) -> list[NightRange]:
    """Runs of consecutive nights, each as one stay (check out the morning after)."""
    ranges: list[NightRange] = []
    for night in sorted(nights):
        if ranges and ranges[-1].check_out == night:
            ranges[-1] = NightRange(check_in=ranges[-1].check_in, check_out=night + timedelta(1))
        else:
            ranges.append(NightRange(check_in=night, check_out=night + timedelta(1)))
    return ranges


def _range_label(nights: NightRange) -> str:
    check_in, check_out = nights.check_in, nights.check_out
    if check_out - check_in == timedelta(days=1):
        return f"the night of {check_in:%b} {check_in.day}"
    return f"{check_in:%b} {check_in.day} to {check_out:%b} {check_out.day}"


def _lodging(plan: PlanSnapshot, offer: Offer | None) -> NextAction | None:
    if plan.preferences.lodging is None:
        return _ask(SlotName.LODGING_PREFERENCES, plan)
    status = stage_status(plan, PlanStage.LODGING)
    uncovered = [i.date for i in status.issues if i.code == IssueCode.UNCOVERED_NIGHT and i.date]
    first = night_ranges(uncovered)[0]
    return _pick_or_search(
        plan,
        offer,
        SlotName.LODGING_STAY,
        "lodging",
        f"Find a place to stay {_range_label(first)}",
        nights=first,
    )


def _activities(plan: PlanSnapshot, offer: Offer | None) -> NextAction | None:
    if plan.preferences.activities is None:
        return _ask(SlotName.ACTIVITY_PREFERENCES, plan)
    return _pick_or_search(plan, offer, SlotName.ACTIVITIES, "activities", "Find things to do")


def _ground(plan: PlanSnapshot, offer: Offer | None) -> NextAction | None:
    gap = plan.gaps[0]
    label = (
        f"Find a way from {describe_place(gap.from_place, gap.after_item_id)} "
        f"to {describe_place(gap.to_place, gap.before_item_id)}"
    )
    return _pick_or_search(plan, offer, SlotName.TRANSFER, "transfers", label, gap=gap)


_RULES: dict[PlanStage, _StageRule] = {
    PlanStage.FLIGHTS: _flights,
    PlanStage.LODGING: _lodging,
    PlanStage.ACTIVITIES: _activities,
    PlanStage.GROUND: _ground,
}


def next_action(plan: PlanSnapshot, offer: Offer | None) -> NextAction:
    """What the plan needs next in its current stage."""
    stage = plan.stage
    if stage == PlanStage.REVIEW:
        return Complete()
    following = STAGES[STAGES.index(stage) + 1]
    if stage == PlanStage.BASICS:
        return _basics(plan, offer) or AdvanceStage(to=following)
    # Every later stage searches with the basics, so a basic removed since is asked first.
    missing = _missing_basic(plan, with_optional=False)
    if missing is not None:
        return _ask(missing, plan)
    # A finished stage advances even without its preferences (items added by hand count,
    # and skipped flights make the flights checklist empty), so the stage rules only run
    # while the checklist has something left.
    if stage_status(plan, stage).ready:
        return AdvanceStage(to=following)
    return _RULES[stage](plan, offer) or AdvanceStage(to=following)


def refresh_search(plan: PlanSnapshot) -> RunSearch | None:
    """The search behind the plan's pending choices, to run again for other options
    ("More options"): what the engine would ask for if the offer were gone. None when the
    plan isn't at choices that can be refreshed."""
    pending = next_action(plan, None)
    if not isinstance(pending, RunSearch) or pending.kind == "transfers":
        return None
    return pending


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def evaluate(
    plan: PlanSnapshot, open_offer: Offer | None, warnings: Sequence[TimelineWarning]
) -> PlanAssessment:
    """The plan's assessment: its issues and the one thing to do next."""
    return PlanAssessment(
        stage=plan.stage,
        issues=plan_issues(plan, warnings),
        next_action=next_action(plan, open_offer),
        can_advance=plan.stage != PlanStage.REVIEW and stage_status(plan, plan.stage).ready,
    )
