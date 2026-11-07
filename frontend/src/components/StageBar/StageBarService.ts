import type { TravelPlan } from '@/api/types.gen'
import { STAGE_META } from '@/lib/stages'

// Pure helpers for StageBar: which "go on" button the planner's next action calls for.

// ----- types -----

export interface ContinueButton {
    label: string
    /**
     * `search`: run (or retry) a search. `advance`: move on from a finished stage.
     * `return`: go back to an earlier stage that a change broke.
     */
    kind: 'search' | 'advance' | 'return'
}

// ----- helpers -----

/**
 * The button that drives the planner on, when its next step needs one: a search to run
 * ("Find flights out"), a finished stage the user came back to ("Continue to Flights"), or
 * an earlier stage that an edit outside a turn broke, e.g. a stay deleted from the item
 * sheet ("Back to Lodging"). Forms and choices have their own controls, so they get none.
 */
export function continueButton(plan: TravelPlan): ContinueButton | null {
    const action = plan.assessment.next_action
    switch (action.type) {
        case 'run_search':
            return { label: action.label, kind: 'search' }
        case 'advance_stage':
            return { label: `Continue to ${STAGE_META[action.to].label}`, kind: 'advance' }
        case 'return_to_stage':
            return { label: `Back to ${STAGE_META[action.to].label}`, kind: 'return' }
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
