import { Badge } from '@mantine/core'
import { IconConfetti } from '@tabler/icons-react'

import type { TravelPlan } from '@/api/types.gen'
import { ProblemRow } from '@/components/ProblemRow/ProblemRow'

import { leftCount, sortProblems, summaryLabel } from './ProblemsPanelService'

// What stands between the trip and a finished plan, across the top of the page: "N things
// left", then each thing the trip is missing with a "Fix this" button (which asks the planner
// to work on it next), then the timeline's warnings. A trip with nothing missing says so
// instead. The page draws the bar's bottom border.

// ------------ Props ------------

interface ProblemsPanelProps {
    plan: TravelPlan
    /** Disables the Fix this buttons, e.g. while a turn streams. */
    disabled?: boolean
    /** Ask the planner to work on this problem next. */
    onFix: (problemId: string) => void
}

export function ProblemsPanel({ plan, disabled = false, onFix }: ProblemsPanelProps) {
    // ------------ Initialization ------------
    const problems = sortProblems(plan.problems)
    const left = leftCount(plan)

    // ------------ Render ------------
    return (
        <section
            aria-label="What the trip still needs"
            className="flex min-w-0 flex-1 items-center gap-3 px-4 py-1.5"
        >
            {plan.complete ? (
                <Badge className="shrink-0" leftSection={<IconConfetti aria-hidden />}>
                    Trip complete
                </Badge>
            ) : (
                <p className="shrink-0 text-xs font-medium">{summaryLabel(left)}</p>
            )}
            {problems.length > 0 && (
                // One row that scrolls sideways, so a long list never pushes the trip down.
                <ul className="flex min-w-0 flex-1 [scrollbar-width:thin] items-center gap-2 overflow-x-auto py-0.5">
                    {problems.map((problem) => (
                        <ProblemRow
                            key={problem.id}
                            problem={problem}
                            disabled={disabled}
                            onFix={onFix}
                        />
                    ))}
                </ul>
            )}
        </section>
    )
}
