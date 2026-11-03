import { Check, Lock, TriangleAlert } from 'lucide-react'

import type { TravelPlan } from '@/api/types.gen'
import { errorMessage } from '@/lib/apiError'
import {
    STAGE_META,
    STAGES,
    stageState,
    stageStatus,
    type PlanStage,
    type StageState,
} from '@/lib/stages'
import { useChangeStage } from '@/lib/useChangeStage'
import { cn } from '@/lib/utils'

// The six planning steps, shown across the top of the page. Passed steps are buttons that
// jump back to them (the server only allows going back). The Next / Back / Skip buttons live
// in StageControls, below the chat.

// ----- types -----

interface StageStepperProps {
    plan: TravelPlan
    /** Disables the step buttons, e.g. while the assistant is replying. */
    disabled?: boolean
}

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

export function StageStepper({ plan, disabled = false }: StageStepperProps) {
    // ----- state -----
    const changeStage = useChangeStage(plan)
    const busy = disabled || changeStage.isPending

    // ----- markup -----
    function renderStep(stage: PlanStage, index: number) {
        const state = stageState(plan, stage)
        const Icon = STATE_ICON[state]
        const { label } = STAGE_META[stage]
        const skipped = stage === 'flights' && plan.flights_skipped && state !== 'current'
        const missing = stageStatus(plan, stage).missing
        const marker = (
            <span
                aria-hidden
                className={cn(
                    'flex size-5 shrink-0 items-center justify-center rounded-full border text-[0.65rem] font-semibold [&_svg]:size-3',
                    state === 'done' && 'border-primary bg-primary text-primary-foreground',
                    state === 'incomplete' && 'border-warning text-warning',
                    state === 'current' && 'border-primary text-primary',
                    state === 'locked' && 'text-muted-foreground',
                )}
            >
                {Icon ? <Icon /> : index + 1}
            </span>
        )
        const text = (
            <span className="truncate">
                {label}
                {skipped && <span className="text-muted-foreground"> (skipped)</span>}
            </span>
        )
        const stateText = STATE_TEXT[state]

        return (
            <li
                key={stage}
                aria-current={state === 'current' ? 'step' : undefined}
                className={cn(
                    'flex min-w-0 items-center gap-1.5 text-xs',
                    state === 'current' ? 'text-foreground font-medium' : 'text-muted-foreground',
                )}
            >
                {state === 'done' || state === 'incomplete' ? (
                    <button
                        type="button"
                        disabled={busy}
                        className="hover:text-foreground flex min-w-0 items-center gap-1.5 rounded-md disabled:pointer-events-none"
                        title={
                            state === 'incomplete' ? `Missing: ${missing.join(', ')}` : undefined
                        }
                        aria-label={`Go back to ${label} (${stateText})`}
                        onClick={() => {
                            changeStage.move({ action: 'go_to', stage })
                        }}
                    >
                        {marker}
                        {text}
                    </button>
                ) : (
                    <span className="flex min-w-0 items-center gap-1.5">
                        {marker}
                        {text}
                        <span className="sr-only"> ({stateText})</span>
                    </span>
                )}
            </li>
        )
    }

    return (
        <nav aria-label="Planning stages" className="flex flex-col gap-1 border-b px-6 py-2.5">
            <ol className="flex items-center gap-4 overflow-x-auto">{STAGES.map(renderStep)}</ol>
            {changeStage.error && (
                <p role="alert" className="text-destructive text-xs">
                    {errorMessage(changeStage.error, "Couldn't change the stage")}
                </p>
            )}
        </nav>
    )
}
