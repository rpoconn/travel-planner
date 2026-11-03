import type { StageStatus, TravelPlan } from '@/api/types.gen'

// The planning workflow as the UI shows it: basics → flights → lodging → activities →
// ground → review. The server decides readiness (`plan.stages`) and moves the plan between
// stages; these helpers only name the stages and say how each one should be drawn.

// ----- types -----

export type PlanStage = TravelPlan['stage']

/**
 * How a stage is drawn in the stepper: `done` (passed and still complete), `incomplete`
 * (passed, but something was removed since), `current`, or `locked` (not reached yet).
 */
export type StageState = 'done' | 'incomplete' | 'current' | 'locked'

export interface StageMeta {
    /** Short name for the stepper. */
    label: string
    /** What the stage is for, shown under the stepper. */
    description: string
    /** Heading of the empty chat while the plan is in this stage. */
    prompt: string
}

// ----- stages -----

export const STAGES: readonly PlanStage[] = [
    'basics',
    'flights',
    'lodging',
    'activities',
    'ground',
    'review',
]

export const STAGE_META: Record<PlanStage, StageMeta> = {
    basics: {
        label: 'Basics',
        description: "Where and when you're going, who's coming, and where the trip starts.",
        prompt: 'Where to next?',
    },
    flights: {
        label: 'Flights',
        description:
            "A flight out and a flight home. Skip this if you're driving or taking a train.",
        prompt: 'How are you getting there?',
    },
    lodging: {
        label: 'Lodging',
        description: 'Somewhere to stay every night of the trip.',
        prompt: 'Where will you stay?',
    },
    activities: {
        label: 'Activities',
        description: "Things to do while you're there.",
        prompt: 'What do you want to do?',
    },
    ground: {
        label: 'Transfers',
        description: 'A way to get between every two places, from leaving home to getting back.',
        prompt: 'How will you get around?',
    },
    review: {
        label: 'Review',
        description: 'Check the whole trip, home to home.',
        prompt: 'Ready to go?',
    },
}

/** Position of `stage` in the workflow (0 for basics). */
export function stageIndex(stage: PlanStage): number {
    return STAGES.indexOf(stage)
}

/** The server's checklist for `stage`; a stage the server didn't report counts as not ready. */
export function stageStatus(plan: TravelPlan, stage: PlanStage): StageStatus {
    return (
        plan.stages.find((status) => status.stage === stage) ?? {
            stage,
            ready: false,
            missing: [],
        }
    )
}

/** The checklist for the stage the plan is in now. */
export function currentStatus(plan: TravelPlan): StageStatus {
    return stageStatus(plan, plan.stage)
}

export function stageState(plan: TravelPlan, stage: PlanStage): StageState {
    const offset = stageIndex(stage) - stageIndex(plan.stage)
    if (offset > 0) return 'locked'
    if (offset === 0) return 'current'
    return stageStatus(plan, stage).ready ? 'done' : 'incomplete'
}

// ----- chat suggestions -----
// Starter messages for an empty chat. Several match the offline fake's scripted commands
// ("Set up the trip", "Add a hotel", "Add transfers"), so they also work without a model.

export const STAGE_SUGGESTIONS: Record<PlanStage, (destination: string | null) => string[]> = {
    basics: (destination) =>
        destination
            ? [
                  `Set up the trip to ${destination}`,
                  `When is the best time to visit ${destination}?`,
                  "It's two adults, leaving from home",
              ]
            : [
                  'I want to spend a week in Tokyo in May',
                  'Plan a long weekend in Lisbon',
                  'Where is warm and walkable in February?',
              ],
    flights: (destination) => [
        destination ? `Find flights to ${destination}` : 'Find flights for this trip',
        'Find the cheapest nonstop round trip',
        'Which days are cheapest to fly?',
    ],
    lodging: (destination) => [
        'Add a hotel',
        destination ? `Suggest a place to stay in ${destination}` : 'Suggest a place to stay',
        'Find somewhere central under $200 a night',
    ],
    activities: (destination) => [
        'What should we do on the first day?',
        destination ? `Add a museum visit in ${destination}` : 'Add a museum visit',
        'Find a dinner spot for the last night',
    ],
    ground: () => [
        'Add transfers',
        'How do we get from the airport to the hotel?',
        'Use taxis for every leg',
    ],
    review: () => ['Summarize the trip', 'Did we forget anything?', 'What will it all cost?'],
}

/** The empty chat's suggestions for the plan's current stage. */
export function suggestionsFor(plan: TravelPlan): string[] {
    const destination = plan.destination?.city ?? plan.destination?.name ?? null
    return STAGE_SUGGESTIONS[plan.stage](destination)
}
