import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
    ArrowLeft,
    ArrowRight,
    Check,
    Lock,
    PartyPopper,
    SkipForward,
    TriangleAlert,
} from 'lucide-react'

import { changeStageMutation } from '@/api/@tanstack/react-query.gen'
import type { StageChange, TravelPlan } from '@/api/types.gen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { applyPlanSnapshot } from '@/lib/planCache'
import {
    currentStatus,
    STAGE_META,
    STAGES,
    stageState,
    stageStatus,
    type PlanStage,
    type StageState,
} from '@/lib/stages'
import { cn } from '@/lib/utils'

// The planning workflow above the chat: six steps, what the current one still needs, and
// the buttons that move the plan. Only the user moves stages (the assistant just suggests
// it); the server checks readiness again and answers 409 with the reason if Next is early.

// ----- types -----

interface StageStepperProps {
    plan: TravelPlan
    /** Disables every button, e.g. while the assistant is replying. */
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
    const queryClient = useQueryClient()
    const changeStage = useMutation({
        ...changeStageMutation(),
        onSuccess: (updated) => {
            applyPlanSnapshot(queryClient, updated)
        },
    })

    const status = currentStatus(plan)
    const meta = STAGE_META[plan.stage]
    const busy = disabled || changeStage.isPending
    const atReview = plan.stage === 'review'

    // ----- event handlers -----
    function move(body: StageChange) {
        changeStage.mutate({ path: { plan_id: plan.id }, body })
    }

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
                {/* Passed stages are links back to them (the server only allows going back). */}
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
                            move({ action: 'go_to', stage })
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
        <nav aria-label="Planning stages" className="flex flex-col gap-2 border-b px-6 py-3">
            <ol className="flex items-center gap-3 overflow-x-auto">{STAGES.map(renderStep)}</ol>

            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                <div className="min-w-0 flex-1 text-xs">
                    <p className="text-muted-foreground">{meta.description}</p>
                    {atReview && status.ready ? (
                        <Badge className="mt-1.5">
                            <PartyPopper aria-hidden />
                            Trip complete
                        </Badge>
                    ) : (
                        status.missing.length > 0 && (
                            <p className="text-foreground mt-0.5" data-testid="stage-missing">
                                <span className="font-medium">Missing:</span>{' '}
                                {status.missing.join(', ')}
                            </p>
                        )
                    )}
                </div>

                <div className="flex shrink-0 items-center gap-1.5">
                    {plan.stage !== 'basics' && (
                        <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => {
                                move({ action: 'back' })
                            }}
                        >
                            <ArrowLeft aria-hidden />
                            Back
                        </Button>
                    )}
                    {plan.stage === 'flights' && (
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() => {
                                move({ action: 'skip_flights' })
                            }}
                        >
                            <SkipForward aria-hidden />
                            Skip flights
                        </Button>
                    )}
                    {!atReview && (
                        <Button
                            size="sm"
                            disabled={busy || !status.ready}
                            onClick={() => {
                                move({ action: 'next' })
                            }}
                        >
                            Next
                            <ArrowRight aria-hidden />
                        </Button>
                    )}
                </div>
            </div>

            {changeStage.isError && (
                <p role="alert" className="text-destructive text-xs">
                    {errorMessage(changeStage.error, "Couldn't change the stage")}
                </p>
            )}
        </nav>
    )
}
