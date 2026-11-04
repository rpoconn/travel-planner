import type { TravelPlan } from '@/api/types.gen'
import { STAGE_META } from '@/lib/stages'

// Pure helpers for StageBar: which "go on" button the planner's next action calls for.

// ----- types -----

export interface ContinueButton {
    label: string
    /** `search`: run (or retry) a search. `advance`: move on from a finished stage. */
    kind: 'search' | 'advance'
}

// ----- helpers -----

/**
 * The button that drives the planner on, when its next step needs one: a search to run
 * ("Find flights out"), or a finished stage the user came back to ("Continue to Flights").
 * Forms and choices have their own controls, so they get none.
 */
export function continueButton(plan: TravelPlan): ContinueButton | null {
    const action = plan.assessment.next_action
    switch (action.type) {
        case 'run_search':
            return { label: action.label, kind: 'search' }
        case 'advance_stage':
            return { label: `Continue to ${STAGE_META[action.to].label}`, kind: 'advance' }
        default:
            return null
    }
}

/** At review with nothing left to fix. */
export function tripComplete(plan: TravelPlan): boolean {
    const { assessment } = plan
    return (
        plan.stage === 'review' &&
        assessment.next_action.type === 'complete' &&
        assessment.issues.every((issue) => issue.severity !== 'blocking')
    )
}
