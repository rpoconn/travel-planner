import { TriangleAlert, Wrench } from 'lucide-react'

import type { Problem } from '@/api/types.gen'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { canFix, fixLabel, problemText } from './ProblemRowService'

// One thing wrong with the trip, as a row of the problems list. Something the trip is
// missing gets a "Fix this" button, which asks the planner to work on it next; a warning (an
// overlap, a cost over a limit) is only listed.

// ------------ Props ------------

interface ProblemRowProps {
    problem: Problem
    /** Disables the button, e.g. while a turn streams. */
    disabled?: boolean
    onFix: (problemId: string) => void
}

export function ProblemRow({ problem, disabled = false, onFix }: ProblemRowProps) {
    // ------------ Initialization ------------
    const blocking = problem.severity === 'blocking'

    // ------------ Render ------------
    return (
        <li
            data-severity={problem.severity}
            className={cn(
                'flex shrink-0 items-center gap-1.5 rounded-full border py-0.5 pr-1 pl-2.5 text-xs',
                blocking ? 'bg-card' : 'text-muted-foreground border-dashed pr-2.5',
            )}
        >
            <TriangleAlert
                aria-hidden
                className={cn('size-3 shrink-0', blocking ? 'text-destructive' : 'text-warning')}
            />
            <span className="whitespace-nowrap">{problemText(problem)}</span>
            {canFix(problem) && (
                <Button
                    size="xs"
                    variant="ghost"
                    className="rounded-full"
                    disabled={disabled}
                    aria-label={fixLabel(problem)}
                    onClick={() => {
                        onFix(problem.id)
                    }}
                >
                    <Wrench aria-hidden />
                    Fix this
                </Button>
            )}
        </li>
    )
}
