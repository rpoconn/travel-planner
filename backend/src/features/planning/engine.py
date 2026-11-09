"""The planning rules engine: given the plan as it stands, what happens next.

Pure functions (no database, no model). The engine reads each stage's checklist
(`plans.stages`): every blocking issue has the slot that fixes it (`slots.FIX_SLOT`), and
the next action works on the first issue left, in slot order. Input slots that fix no
issue (preferences, the optional budget) are asked while the plan holds no answer for
them. Every decision reads itinerary state and typed slot values, never text.

| Stage      | Issues, in slot order                    | Asked first                |
|------------|------------------------------------------|----------------------------|
| basics     | destination, dates, travelers, origin    | budget (optional, after)   |
| flights    | flight out, flight home (or skipped)     | flight preferences         |
| lodging    | each run of uncovered nights             | lodging preferences        |
| activities | no activities                            | activity preferences       |
| ground     | each gap without a transfer              |                            |
| review     | (complete)                               |                            |

A pick slot shows the open offer when it is for that slot, stage, plan version and
gap/nights; otherwise it asks for a search. Later stages first re-ask any basics they
depend on (a stage can't search for flights without dates), and go back to any earlier
stage whose checklist has an issue again (a stay removed at review leaves a night
uncovered), since a stage can only be left once its checklist is empty.
"""

from collections.abc import Sequence
from datetime import date, timedelta

from features.itinerary.gaps import describe_place
from features.itinerary.schemas import Gap, ItemType, TimelineWarning
from features.planning.schemas import (
    AdvanceStage,
    AskInput,
    Complete,
    NextAction,
    NightRange,
    Offer,
    OfferChoice,
    PlanAssessment,
    ReturnToStage,
    RunSearch,
)
from features.planning.slots import FIX_SLOT, SLOT_ORDER, SLOTS, STAGE_SLOTS, SlotSpec
from features.plans.schemas import (
    STAGES,
    Issue,
    PlanSnapshot,
    PlanStage,
    SlotName,
)
from features.plans.stages import stage_status
from shared.domain import IssueCode

# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

# Timeline warnings that don't become warning issues: the lodging and ground checklists
# already report them as blocking issues.
_CHECKLIST_WARNINGS = frozenset({IssueCode.UNCOVERED_NIGHT, IssueCode.MISSING_TRANSFER})

# The stage whose items a warning is about, by the type of its first item.
_ITEM_STAGES: dict[ItemType, PlanStage] = {
    ItemType.FLIGHT: PlanStage.FLIGHTS,
    ItemType.LODGING: PlanStage.LODGING,
    ItemType.ACTIVITY: PlanStage.ACTIVITIES,
    ItemType.TRANSFER: PlanStage.GROUND,
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


def _ask(name: SlotName, plan: PlanSnapshot) -> AskInput:
    spec = SLOTS[name]
    answer = spec.read(plan) if spec.read is not None else None
    return AskInput(
        slot=name,
        stage=spec.stage,
        prompt=spec.prompt,
        fields=list(spec.fields),
        optional=spec.optional,
        prefill=answer.model_dump(mode="json") if answer is not None else {},
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


def night_ranges(nights: Sequence[date]) -> list[NightRange]:
    """Runs of consecutive nights, each as one stay (check out the morning after)."""
    ranges: list[NightRange] = []
    for night in sorted(nights):
        if ranges and ranges[-1].check_out == night:
            ranges[-1] = NightRange(check_in=ranges[-1].check_in, check_out=night + timedelta(1))
        else:
            ranges.append(NightRange(check_in=night, check_out=night + timedelta(1)))
    return ranges


def range_label(nights: NightRange) -> str:
    check_in, check_out = nights.check_in, nights.check_out
    if check_out - check_in == timedelta(days=1):
        return f"the night of {check_in:%b} {check_in.day}"
    return f"{check_in:%b} {check_in.day} to {check_out:%b} {check_out.day}"


def _search_label(slot: SlotName, gap: Gap | None, nights: NightRange | None) -> str:
    """The search button's words, e.g. "Find flights out"."""
    match slot:
        case SlotName.FLIGHT_OUTBOUND:
            return "Find flights out"
        case SlotName.FLIGHT_RETURN:
            return "Find flights home"
        case SlotName.LODGING_STAY if nights is not None:
            return f"Find a place to stay {range_label(nights)}"
        case SlotName.ACTIVITIES:
            return "Find things to do"
        case SlotName.TRANSFER if gap is not None:
            return (
                f"Find a way from {describe_place(gap.from_place, gap.after_item_id)} "
                f"to {describe_place(gap.to_place, gap.before_item_id)}"
            )
    return "Find options"


def _pick_or_search(
    plan: PlanSnapshot,
    offer: Offer | None,
    slot: SlotName,
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
            can_refresh=spec.refreshable,
        )
    label = _search_label(slot, gap, nights)
    return RunSearch(slot=slot, stage=spec.stage, label=label, gap=gap, nights=nights)


def _fix(plan: PlanSnapshot, offer: Offer | None, issues: Sequence[Issue]) -> NextAction:
    """The action that works on the first of `issues` (a stage's checklist, in slot
    order): ask its fix slot's form, or pick from (or search for) its candidates."""
    issue = issues[0]
    slot = FIX_SLOT[issue.code]
    if SLOTS[slot].kind == "input":
        return _ask(slot, plan)
    nights = None
    if issue.code == IssueCode.UNCOVERED_NIGHT:
        # One stay for the first run of consecutive uncovered nights.
        uncovered = [i.date for i in issues if i.code == IssueCode.UNCOVERED_NIGHT and i.date]
        nights = night_ranges(uncovered)[0]
    return _pick_or_search(plan, offer, slot, gap=issue.gap, nights=nights)


# ---------------------------------------------------------------------------
# Next action
# ---------------------------------------------------------------------------


def _fixable(plan: PlanSnapshot, stage: PlanStage) -> list[Issue]:
    """The stage's checklist issues that a slot fixes, in the order the slots are asked.
    The sort is stable, so the issues of one slot (several nights, several gaps) keep
    their order."""
    issues = [issue for issue in stage_status(plan, stage).issues if issue.code in FIX_SLOT]
    return sorted(issues, key=lambda issue: SLOT_ORDER[FIX_SLOT[issue.code]])


def _broken_earlier(plan: PlanSnapshot) -> ReturnToStage | None:
    """Go back to the first stage after basics, before the current one, whose checklist has
    a fixable issue again. Basics are re-asked in place instead (`next_action` rule 2), and
    `next` only leaves a stage with an empty checklist, so such an issue can only come
    from a later change to the plan (a stay removed, a flight's dates moved)."""
    current = STAGES.index(plan.stage)
    for stage in STAGES[1:current]:
        issues = _fixable(plan, stage)
        if issues:
            return ReturnToStage(to=stage, reason=issues[0].message)
    return None


def _unanswered(plan: PlanSnapshot, stage: PlanStage) -> SlotName | None:
    """The stage's first input slot that fixes no issue (preferences, the budget) and that
    the plan holds no answer for, leaving out declined ones."""
    declined = plan.preferences.declined
    for name in STAGE_SLOTS[stage]:
        spec = SLOTS[name]
        if spec.fixes is not None or spec.read is None or name in declined:
            continue
        if spec.read(plan) is None:
            return name
    return None


def next_action(plan: PlanSnapshot, offer: Offer | None) -> NextAction:
    """What the plan needs next in its current stage:

    1. A missing basic, in any stage (every later stage searches with the basics).
    2. In basics with none missing: the optional budget, unless answered or declined.
    3. An earlier stage whose checklist has an issue again: go back to it.
    4. At review, nothing: `Complete`.
    5. A later stage with an empty checklist advances, even without its preferences
       (items added by hand count, and skipped flights empty the flights checklist).
    6. The stage's preferences, before it searches.
    7. The stage's first issue: pick from its offer, or search for one.
    """
    stage = plan.stage
    basics = _fixable(plan, PlanStage.BASICS)
    if basics:
        return _fix(plan, offer, basics)
    if stage == PlanStage.BASICS:
        optional = _unanswered(plan, stage)
        return _ask(optional, plan) if optional is not None else AdvanceStage(to=STAGES[1])
    broken = _broken_earlier(plan)
    if broken is not None:
        return broken
    if stage == PlanStage.REVIEW:
        return Complete()
    following = STAGES[STAGES.index(stage) + 1]
    issues = _fixable(plan, stage)
    if not issues:
        return AdvanceStage(to=following)
    preferences = _unanswered(plan, stage)
    if preferences is not None:
        return _ask(preferences, plan)
    return _fix(plan, offer, issues)


def refresh_search(plan: PlanSnapshot) -> RunSearch | None:
    """The search behind the plan's pending choices, to run again for other options
    ("More options"): what the engine would ask for if the offer were gone. None when the
    plan isn't at choices that can be refreshed."""
    pending = next_action(plan, None)
    if not isinstance(pending, RunSearch) or not SLOTS[pending.slot].refreshable:
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
