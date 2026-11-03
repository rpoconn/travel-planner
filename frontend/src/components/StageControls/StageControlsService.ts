import type { TravelPlan } from '@/api/types.gen'
import { currentStatus, type PlanStage } from '@/lib/stages'

// The stage's "next step" button: what to ask the assistant so the current stage gets done
// (find flights, find a hotel, suggest things to do, …). The text is sent as an ordinary chat
// message, so it reads like something the user typed and names exactly what is missing.
//
// Several messages start with the offline fake's scripted commands ("Set up the trip",
// "Add a hotel", "Add transfers"), so the button also works with LLM_PROVIDER=fake.

// ----- types -----

export interface NextStep {
    /** Button text, e.g. "Find flights". */
    label: string
    /** The chat message the button sends. */
    message: string
    /**
     * True when the stage still needs this step, so it is the main button and Next waits.
     * False when the stage is already done and the step is optional (more activities, a
     * summary at review); Next is then the main button.
     */
    primary: boolean
}

// ----- helpers -----

function destinationName(plan: TravelPlan): string | null {
    return plan.destination?.city ?? plan.destination?.name ?? null
}

// ----- next step -----

/**
 * The step the assistant can take next in the plan's current stage, or null when there is
 * nothing for it to do there (the stage is done; Next is the step).
 */
export function nextStep(plan: TravelPlan): NextStep | null {
    const status = currentStatus(plan)
    const missing = status.missing
    const needed = missing.length > 0 ? ` Still needed: ${missing.join(', ')}.` : ''
    const destination = destinationName(plan)
    const stage: PlanStage = plan.stage

    // The one missing leg, named in the flights label ("Find a flight home").
    const onlyMissing = missing.length === 1 ? missing[0] : undefined

    switch (stage) {
        case 'basics':
            if (status.ready) return null
            return {
                label: 'Set up the trip',
                message: `Set up the trip${destination ? ` to ${destination}` : ''}.${needed}`,
                primary: true,
            }
        case 'flights':
            if (status.ready) return null
            return {
                label: onlyMissing ? `Find ${onlyMissing}` : 'Find flights',
                message:
                    `Find flights for the trip${destination ? ` to ${destination}` : ''}.` + needed,
                primary: true,
            }
        case 'lodging':
            if (status.ready) return null
            return {
                label: 'Find a hotel',
                message: `Add a hotel${destination ? ` in ${destination}` : ''}.${needed}`,
                primary: true,
            }
        case 'activities':
            // Always offered: one activity finishes the stage, but most trips want more.
            return {
                label: status.ready ? 'Suggest more' : 'Suggest activities',
                message:
                    `Suggest a few things to do${destination ? ` in ${destination}` : ''}, ` +
                    'including a good restaurant, and add the best ones to the trip.',
                primary: !status.ready,
            }
        case 'ground':
            if (status.ready) return null
            return {
                label: 'Add transfers',
                message: `Add transfers for every leg of the trip.${needed}`,
                primary: true,
            }
        case 'review':
            // Review has no tools that change the plan, so the assistant explains instead.
            return status.ready
                ? { label: 'Summarize the trip', message: 'Summarize the trip.', primary: false }
                : {
                      label: "What's missing?",
                      message: `What is still missing from the trip, and how do I finish it?${needed}`,
                      primary: true,
                  }
    }
}
