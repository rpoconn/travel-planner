"""The validation engine: what stands between the plan and a finished trip.

A pure function (no database, no model). `validate` returns a flat list of problems and
nothing else: it does not say what to do next, and it moves nothing. Whoever drives the
plan (the model, through the `validate_plan` tool) decides which problem to work on; the
plan is complete once no blocking problem is left.

Blocking problems come from the plan's checklists (`plans.stages.plan_progress`), in the
order their fixes are usually made: the basics, the flights, the stays, the activities,
then the rides between them. Warnings come from the timeline (`itinerary.timeline`):
overlaps, costs over a budget, items outside the trip. They don't block.
"""

from collections.abc import Sequence
from datetime import date, timedelta

from features.itinerary.schemas import TimelineWarning
from features.planning.schemas import NightRange, Problem, ProblemFix
from features.planning.slots import FIX_SLOT, SLOT_ORDER, SLOTS
from features.plans.schemas import Issue, PlanSnapshot, PlanStage
from shared.domain import IssueCode

# Timeline warnings that aren't reported as warnings: the checklists already report them
# as blocking problems.
CHECKLIST_WARNINGS = frozenset({IssueCode.UNCOVERED_NIGHT, IssueCode.MISSING_TRANSFER})

# ---------------------------------------------------------------------------
# Night runs
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------


def _fix(code: IssueCode) -> ProblemFix:
    """How a problem is usually fixed: by asking the slot that answers it, by searching for
    candidates to pick from, or (no slot) by editing the items it names."""
    slot = FIX_SLOT.get(code)
    if slot is None:
        return ProblemFix(how="edit")
    return ProblemFix(how="ask" if SLOTS[slot].kind == "input" else "search", slot=slot)


def _checklist_problem(issue: Issue) -> Problem:
    problem_id = issue.code.value
    if issue.gap is not None:
        # Home is not an item, so a gap end without an item is home.
        ends = (issue.gap.after_item_id or "home", issue.gap.before_item_id or "home")
        problem_id = f"{issue.code.value}:{ends[0]}:{ends[1]}"
    return Problem(
        id=problem_id,
        code=issue.code,
        severity="blocking",
        message=issue.message,
        date=issue.date,
        item_ids=issue.item_ids,
        gap=issue.gap,
        fix=_fix(issue.code),
    )


def _night_problems(issues: Sequence[Issue]) -> list[Problem]:
    """One problem per run of uncovered nights: a run is fixed by one stay."""
    nights = [issue.date for issue in issues if issue.date is not None]
    return [
        Problem(
            id=f"{IssueCode.UNCOVERED_NIGHT.value}:{run.check_in.isoformat()}",
            code=IssueCode.UNCOVERED_NIGHT,
            severity="blocking",
            message=f"a place to stay {range_label(run)}",
            date=run.check_in,
            nights=run,
            fix=_fix(IssueCode.UNCOVERED_NIGHT),
        )
        for run in night_ranges(nights)
    ]


def _blocking(plan: PlanSnapshot) -> list[Problem]:
    # Review's checklist only repeats the stages before it.
    issues = [
        issue
        for status in plan.stages
        if status.stage != PlanStage.REVIEW
        for issue in status.issues
    ]
    uncovered = [issue for issue in issues if issue.code == IssueCode.UNCOVERED_NIGHT]
    others = [issue for issue in issues if issue.code != IssueCode.UNCOVERED_NIGHT]
    problems = [_checklist_problem(issue) for issue in others] + _night_problems(uncovered)
    # The order their fix slots are asked in (stable, so nights and gaps keep trip order).
    last = len(SLOT_ORDER)
    problems.sort(key=lambda p: SLOT_ORDER[p.fix.slot] if p.fix.slot is not None else last)
    return problems


def _warning_problem(warning: TimelineWarning) -> Problem:
    parts = [warning.kind.value]
    if warning.date is not None:
        parts.append(warning.date.isoformat())
    parts += [str(item_id) for item_id in warning.item_ids]
    return Problem(
        id=":".join(parts),
        code=warning.kind,
        severity="warning",
        message=warning.message,
        date=warning.date,
        item_ids=warning.item_ids,
        fix=_fix(warning.kind),
    )


def _unique(problems: Sequence[Problem]) -> list[Problem]:
    """`problems` with every id used once. The same problem reported twice (missing dates,
    by the basics and by the stays) is kept once; different ones that share an id (two
    spending limits exceeded, neither about an item) are numbered."""
    kept: dict[str, Problem] = {}
    for problem in problems:
        twin = kept.get(problem.id)
        if twin is None:
            kept[problem.id] = problem
        elif twin.model_dump(exclude={"id"}) != problem.model_dump(exclude={"id"}):
            number = 2
            while f"{problem.id}#{number}" in kept:
                number += 1
            renamed = problem.model_copy(update={"id": f"{problem.id}#{number}"})
            kept[renamed.id] = renamed
    return list(kept.values())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def validate(plan: PlanSnapshot, warnings: Sequence[TimelineWarning]) -> list[Problem]:
    """Everything wrong with the plan as it stands: blocking problems first, then the
    timeline's warnings."""
    flagged = [_warning_problem(w) for w in warnings if w.kind not in CHECKLIST_WARNINGS]
    return _unique([*_blocking(plan), *flagged])


def is_complete(problems: Sequence[Problem]) -> bool:
    """A plan is complete when nothing blocking is left; warnings are the user's call."""
    return not any(problem.severity == "blocking" for problem in problems)
