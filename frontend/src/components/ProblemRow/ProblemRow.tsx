import { Button } from '@mantine/core'
import { clsx } from 'clsx'
import { IconAlertTriangle, IconTool } from '@tabler/icons-react'

import type { Problem } from '@/api/types.gen'

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
            className={clsx(
                'flex shrink-0 items-center gap-1.5 rounded-full border py-0.5 pl-2.5 text-xs',
                // A blocking row ends in its button, so it sits closer to the edge.
                blocking ? 'bg-card pr-1' : 'text-muted-foreground border-dashed pr-2.5',
            )}
        >
            <IconAlertTriangle
                aria-hidden
                className={clsx('size-3 shrink-0', blocking ? 'text-destructive' : 'text-warning')}
            />
            <span className="whitespace-nowrap">{problemText(problem)}</span>
            {canFix(problem) && (
                <Button
                    size="compact-xs"
                    variant="subtle"
                    radius="xl"
                    disabled={disabled}
                    aria-label={fixLabel(problem)}
                    leftSection={<IconTool aria-hidden />}
                    onClick={() => {
                        onFix(problem.id)
                    }}
                >
                    Fix this
                </Button>
            )}
        </li>
    )
}
