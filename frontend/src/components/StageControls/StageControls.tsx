import { ArrowLeft, ArrowRight, PartyPopper, SkipForward, Sparkles } from 'lucide-react'

import type { TravelPlan } from '@/api/types.gen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { currentStatus, STAGE_META } from '@/lib/stages'
import { useChangeStage } from '@/lib/useChangeStage'

import { nextStep } from './StageControlsService'

// What the current planning stage is about, what it still needs, and the buttons that move
// the plan (Back / Skip flights / Next). Rendered inline at the end of the chat transcript, like
// a message from the assistant asking "ready to move on?"; the step list is StageStepper.
// Only the user moves stages (the assistant just suggests it); the server checks readiness
// again and answers 409 with the reason if Next is early.
// While the stage still needs something, a next-step button ("Find flights", "Find a hotel",
// …) asks the assistant to do it, so there is always something to press besides Skip.

// ----- types -----

interface StageControlsProps {
    plan: TravelPlan
    /** Disables every button, e.g. while the assistant is replying. */
    disabled?: boolean
    /** Sends the next-step button's message to the assistant as a chat message. */
    onAsk: (message: string) => void
}

export function StageControls({ plan, disabled = false, onAsk }: StageControlsProps) {
    // ----- state -----
    const changeStage = useChangeStage(plan)
    const status = currentStatus(plan)
    const meta = STAGE_META[plan.stage]
    const busy = disabled || changeStage.isPending
    const atReview = plan.stage === 'review'
    const step = nextStep(plan)

    // ----- markup -----
    return (
        <section
            aria-label="Stage controls"
            className="bg-card flex flex-col gap-1 rounded-xl border px-4 py-3"
        >
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
                                changeStage.move({ action: 'back' })
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
                                changeStage.move({ action: 'skip_flights' })
                            }}
                        >
                            <SkipForward aria-hidden />
                            Skip flights
                        </Button>
                    )}
                    {step && (
                        <Button
                            size="sm"
                            variant={step.primary ? 'default' : 'outline'}
                            disabled={busy}
                            onClick={() => {
                                onAsk(step.message)
                            }}
                        >
                            <Sparkles aria-hidden />
                            {step.label}
                        </Button>
                    )}
                    {/* Next is the main button only once it can be pressed; until then the
                        next-step button is, so a disabled Next doesn't look like the action. */}
                    {!atReview && (
                        <Button
                            size="sm"
                            variant={status.ready ? 'default' : 'outline'}
                            disabled={busy || !status.ready}
                            onClick={() => {
                                changeStage.move({ action: 'next' })
                            }}
                        >
                            Next
                            <ArrowRight aria-hidden />
                        </Button>
                    )}
                </div>
            </div>

            {changeStage.error && (
                <p role="alert" className="text-destructive text-xs">
                    {errorMessage(changeStage.error, "Couldn't change the stage")}
                </p>
            )}
        </section>
    )
}
