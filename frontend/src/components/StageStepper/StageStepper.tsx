import type { TravelPlan } from '@/api/types.gen'
import { StageStep } from '@/components/StageStep/StageStep'
import { errorMessage } from '@/lib/apiError'
import { STAGES } from '@/lib/stages'
import { useChangeStage } from '@/lib/useChangeStage'

// The six planning steps, shown across the top of the page. Passed steps are buttons that
// jump back to them (the server only allows going back). The Next / Back / Skip buttons live
// in StageControls, in the chat. The page draws the bar's bottom border, since it shares the
// row with the warnings button.

// ----- types -----

interface StageStepperProps {
    plan: TravelPlan
    /** Disables the step buttons, e.g. while the assistant is replying. */
    disabled?: boolean
}

export function StageStepper({ plan, disabled = false }: StageStepperProps) {
    // ----- state -----
    const changeStage = useChangeStage(plan)
    const busy = disabled || changeStage.isPending

    // ----- markup -----
    return (
        <nav
            aria-label="Planning stages"
            className="flex min-w-0 flex-1 flex-col gap-1 px-4 py-1.5"
        >
            <ol className="flex items-center gap-4 overflow-x-auto">
                {STAGES.map((stage, index) => (
                    <StageStep
                        key={stage}
                        plan={plan}
                        stage={stage}
                        index={index}
                        disabled={busy}
                        onGoTo={(target) => {
                            changeStage.move({ action: 'go_to', stage: target })
                        }}
                    />
                ))}
            </ol>
            {changeStage.error && (
                <p role="alert" className="text-destructive text-xs">
                    {errorMessage(changeStage.error, "Couldn't change the stage")}
                </p>
            )}
        </nav>
    )
}
