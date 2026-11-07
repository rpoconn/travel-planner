import { ArrowLeft, ArrowRight, PartyPopper, Search, SkipForward, Undo2 } from 'lucide-react'

import type { TravelPlan } from '@/api/types.gen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { STAGE_META } from '@/lib/stages'
import { useChangeStage } from '@/lib/useChangeStage'

import { continueButton, tripComplete } from './StageBarService'

// The current stage in the planning flow: what it's for, Back, Skip flights, and the button
// that drives the planner on when its next step isn't a form ("Find flights out", "Continue
// to Lodging", "Back to Lodging" after an edit broke an earlier stage). There is no Next:
// the planner moves on by itself once a stage is done.

// ----- types -----

interface StageBarProps {
    plan: TravelPlan
    /** Disables every button, e.g. while a turn streams. */
    disabled?: boolean
    /** Drive the planner on (`POST /planning/continue`). */
    onContinue: () => void
}

export function StageBar({ plan, disabled = false, onContinue }: StageBarProps) {
    // ----- state -----
    const changeStage = useChangeStage(plan)
    const busy = disabled || changeStage.isPending
    const next = continueButton(plan)
    const complete = tripComplete(plan)

    // ----- markup -----
    return (
        <section
            aria-label="Stage controls"
            className="bg-card flex flex-col gap-1 rounded-xl border px-4 py-3"
        >
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                <div className="min-w-0 flex-1 text-xs">
                    <p className="text-muted-foreground">{STAGE_META[plan.stage].description}</p>
                    {complete && (
                        <Badge className="mt-1.5">
                            <PartyPopper aria-hidden />
                            Trip complete
                        </Badge>
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
                    {next && (
                        <Button size="sm" disabled={busy} onClick={onContinue}>
                            {next.kind === 'search' ? (
                                <Search aria-hidden />
                            ) : next.kind === 'return' ? (
                                <Undo2 aria-hidden />
                            ) : (
                                <ArrowRight aria-hidden />
                            )}
                            {next.label}
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
