import { PlanStage, type StageStatus, type TravelPlan } from '@/api/types.gen'

// The planning workflow as the UI shows it: basics → flights → lodging → activities →
// ground → review. The server decides readiness (`plan.stages`) and moves the plan between
// stages; these helpers only name the stages and say how each one should be drawn.

// ----- types -----

// The generated enum is both a constant and a type; callers here only need the type.
export type { PlanStage }

/**
 * How a stage is drawn in the stepper: `done` (passed and still complete), `incomplete`
 * (passed, but something was removed since), `current`, or `locked` (not reached yet).
 */
export type StageState = 'done' | 'incomplete' | 'current' | 'locked'

interface StageMeta {
    /** Short name for the stepper. */
    label: string
    /** What the stage is for, shown under the stepper. */
    description: string
}

// ----- stages -----

// In workflow order: the backend enum declares the stages in order and the generator keeps it.
export const STAGES: readonly PlanStage[] = Object.values(PlanStage)

export const STAGE_META: Record<PlanStage, StageMeta> = {
    basics: {
        label: 'Basics',
        description: "Where and when you're going, who's coming, and where the trip starts.",
    },
    flights: {
        label: 'Flights',
        description:
            "A flight out and a flight home. Skip this if you're driving or taking a train.",
    },
    lodging: {
        label: 'Lodging',
        description: 'Somewhere to stay every night of the trip.',
    },
    activities: {
        label: 'Activities',
        description: "Things to do while you're there.",
    },
    ground: {
        label: 'Transfers',
        description: 'A way to get between every two places, from leaving home to getting back.',
    },
    review: {
        label: 'Review',
        description: 'Check the whole trip, home to home.',
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
            issues: [],
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
