import { CircleCheck, TriangleAlert, Undo2 } from 'lucide-react'
import { useState } from 'react'

import type { Issue, PlanStage, TravelPlan } from '@/api/types.gen'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { errorMessage } from '@/lib/apiError'
import { cn } from '@/lib/utils'
import { useChangeStage } from '@/lib/useChangeStage'

import {
    fixStage,
    goToLabel,
    issueText,
    sortIssues,
    summaryLabel,
    visibleIssues,
} from './IssuesPanelService'

// What the planner found wrong with the trip: things missing from the stages reached so far
// (blocking) and the timeline's warnings. A blocking issue from an earlier stage gets a "Go
// to {stage}" button, which moves the plan back there (`go_to`) so the planner asks for it
// again; one from the current stage is only listed. Shown as "N things to check" at the end of the stage bar (a flyout), and `inline`
// on the review stage, where an empty list is the good news.

// ----- types -----

interface IssuesPanelProps {
    plan: TravelPlan
    /** List in place (the review stage) instead of behind a button. */
    inline?: boolean
    /** Disables the Go to buttons, e.g. while a turn streams. */
    disabled?: boolean
}

export function IssuesPanel({ plan, inline = false, disabled = false }: IssuesPanelProps) {
    // ----- state -----
    const changeStage = useChangeStage(plan)
    const [open, setOpen] = useState(false)
    const issues = sortIssues(visibleIssues(plan))
    const summary = summaryLabel(issues.length)
    const busy = disabled || changeStage.isPending

    // ----- event handlers -----
    function goTo(stage: PlanStage) {
        // The stage moves under the flyout, so get it out of the way.
        setOpen(false)
        changeStage.move({ action: 'go_to', stage })
    }

    // ----- markup -----
    // The list is the same in both places; `inline` only changes what holds it.
    const list = (
        <ul className="flex flex-col gap-1.5 text-xs">
            {issues.map((issue: Issue, index) => {
                const stage = fixStage(issue, plan)
                return (
                    // Issues have no id; their order is stable for a given plan version.
                    <li
                        key={`${issue.code}-${String(index)}`}
                        className="flex items-center justify-between gap-2"
                    >
                        <span
                            className={cn(
                                'flex items-start gap-1.5',
                                issue.severity === 'blocking'
                                    ? 'text-foreground'
                                    : 'text-muted-foreground',
                            )}
                        >
                            <TriangleAlert
                                aria-hidden
                                className={cn(
                                    'mt-0.5 size-3 shrink-0',
                                    issue.severity === 'blocking'
                                        ? 'text-destructive'
                                        : 'text-warning',
                                )}
                            />
                            {issueText(issue)}
                        </span>
                        {stage && (
                            <Button
                                size="xs"
                                variant="ghost"
                                className="shrink-0"
                                disabled={busy}
                                aria-label={`${goToLabel(stage)}: ${issue.message}`}
                                onClick={() => {
                                    goTo(stage)
                                }}
                            >
                                <Undo2 aria-hidden />
                                {goToLabel(stage)}
                            </Button>
                        )}
                    </li>
                )
            })}
        </ul>
    )
    const failure = changeStage.error ? (
        <p role="alert" className="text-destructive text-xs">
            {errorMessage(changeStage.error, "Couldn't change the stage")}
        </p>
    ) : null

    if (inline) {
        return (
            <section
                aria-label="Before you go"
                className="bg-card flex flex-col gap-2 rounded-xl border px-4 py-3"
            >
                <p className="font-heading text-base">Before you go</p>
                {issues.length === 0 ? (
                    <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
                        <CircleCheck aria-hidden className="text-primary size-3.5" />
                        Nothing left to fix: every night has a bed and every move a ride.
                    </p>
                ) : (
                    <>
                        <p className="text-muted-foreground text-xs">{summary}</p>
                        {list}
                    </>
                )}
                {failure}
            </section>
        )
    }

    if (issues.length === 0) return failure

    // A refused move is shown beside the button: the flyout closed when it was pressed.
    return (
        <div className="flex shrink-0 items-center gap-2">
            {failure}
            <Popover open={open} onOpenChange={setOpen}>
                <PopoverTrigger asChild>
                    <Button size="sm" variant="ghost" className="text-warning shrink-0">
                        <TriangleAlert aria-hidden />
                        {summary}
                    </Button>
                </PopoverTrigger>
                <PopoverContent align="end" aria-label="Things to check" className="w-96 p-3">
                    <p className="mb-2 text-xs font-medium">{summary}</p>
                    {list}
                </PopoverContent>
            </Popover>
        </div>
    )
}
