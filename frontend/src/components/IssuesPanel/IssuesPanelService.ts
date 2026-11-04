import type { Issue, PlanStage, TravelPlan } from '@/api/types.gen'
import { STAGE_META, stageIndex } from '@/lib/stages'

// Pure helpers for IssuesPanel: which of the planner's issues to list, and which of them a
// "Go to" button can send the user back to fix.

// ----- listing -----

/**
 * The issues worth listing: every warning (an overlap, a cost over a limit), and anything
 * missing from a stage the plan has already passed (a hotel removed since). The current
 * stage's own checklist is left out: the planner is already asking about it.
 */
export function visibleIssues(plan: TravelPlan): Issue[] {
    const current = stageIndex(plan.stage)
    return plan.assessment.issues.filter(
        (issue) => issue.severity === 'warning' || stageIndex(issue.stage) < current,
    )
}

/** Blocking issues first (they keep the trip from being complete), then warnings. */
export function sortIssues(issues: readonly Issue[]): Issue[] {
    const blocking = issues.filter((issue) => issue.severity === 'blocking')
    return [...blocking, ...issues.filter((issue) => issue.severity !== 'blocking')]
}

/** "1 thing to check", "3 things to check". */
export function summaryLabel(count: number): string {
    return `${String(count)} thing${count === 1 ? '' : 's'} to check`
}

// ----- fixing -----

/**
 * The earlier stage to go back to for a blocking issue, or null. Warnings are listed but
 * not sent anywhere: nothing is missing, so the stage would just move on again.
 */
export function fixStage(issue: Issue, plan: TravelPlan): PlanStage | null {
    if (issue.severity !== 'blocking') return null
    return stageIndex(issue.stage) < stageIndex(plan.stage) ? issue.stage : null
}

/** The Go to button's label: "Go to Lodging". */
export function goToLabel(stage: PlanStage): string {
    return `Go to ${STAGE_META[stage].label}`
}

/**
 * The issue as a line of the list. A blocking issue's message names what's missing ("a
 * flight home"), so it reads "Missing a flight home"; a warning's is a sentence already.
 */
export function issueText(issue: Issue): string {
    return issue.severity === 'blocking' ? `Missing ${issue.message}` : issue.message
}
