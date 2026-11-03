import { Check, Lock, TriangleAlert } from 'lucide-react'

import type { TravelPlan } from '@/api/types.gen'
import { STAGE_META, stageState, stageStatus, type PlanStage, type StageState } from '@/lib/stages'
import { cn } from '@/lib/utils'

// One step in StageStepper. Passed (done / incomplete) steps are buttons that jump back to
// them; current and locked steps are plain text.

// ----- types -----

interface StageStepProps {
    plan: TravelPlan
    stage: PlanStage
    /** Zero-based position, shown as the number on the current step's marker. */
    index: number
    /** Disables the jump-back button. */
    disabled?: boolean
    onGoTo: (stage: PlanStage) => void
}

// ----- constants -----

const STATE_ICON: Record<StageState, typeof Check | null> = {
    done: Check,
    incomplete: TriangleAlert,
    current: null, // the step number
    locked: Lock,
}

/** The step's state for screen readers. */
const STATE_TEXT: Record<StageState, string> = {
    done: 'done',
    incomplete: 'needs attention',
    current: 'current',
    locked: 'locked',
}

export function StageStep({ plan, stage, index, disabled = false, onGoTo }: StageStepProps) {
    // ----- state -----
    const state = stageState(plan, stage)
    const Icon = STATE_ICON[state]
    const { label } = STAGE_META[stage]
    const skipped = stage === 'flights' && plan.flights_skipped && state !== 'current'
    const missing = stageStatus(plan, stage).missing
    const stateText = STATE_TEXT[state]
    const markerClass = cn(
        'flex size-5 shrink-0 items-center justify-center rounded-full border text-[0.65rem] font-semibold [&_svg]:size-3',
        state === 'done' && 'border-primary bg-primary text-primary-foreground',
        state === 'incomplete' && 'border-warning text-warning',
        state === 'current' && 'border-primary text-primary',
        state === 'locked' && 'text-muted-foreground',
    )

    // ----- markup -----
    return (
        <li
            aria-current={state === 'current' ? 'step' : undefined}
            className={cn(
                'flex min-w-0 items-center gap-1.5 text-xs',
                state === 'current' ? 'text-foreground font-medium' : 'text-muted-foreground',
            )}
        >
            {state === 'done' || state === 'incomplete' ? (
                <button
                    type="button"
                    disabled={disabled}
                    className="hover:text-foreground flex min-w-0 items-center gap-1.5 rounded-md disabled:pointer-events-none"
                    title={state === 'incomplete' ? `Missing: ${missing.join(', ')}` : undefined}
                    aria-label={`Go back to ${label} (${stateText})`}
                    onClick={() => {
                        onGoTo(stage)
                    }}
                >
                    <span aria-hidden className={markerClass}>
                        {Icon ? <Icon /> : index + 1}
                    </span>
                    <span className="truncate">
                        {label}
                        {skipped && <span className="text-muted-foreground"> (skipped)</span>}
                    </span>
                </button>
            ) : (
                <span className="flex min-w-0 items-center gap-1.5">
                    <span aria-hidden className={markerClass}>
                        {Icon ? <Icon /> : index + 1}
                    </span>
                    <span className="truncate">
                        {label}
                        {skipped && <span className="text-muted-foreground"> (skipped)</span>}
                    </span>
                    <span className="sr-only"> ({stateText})</span>
                </span>
            )}
        </li>
    )
}
