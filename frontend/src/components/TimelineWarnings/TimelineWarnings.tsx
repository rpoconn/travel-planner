import { useQuery } from '@tanstack/react-query'
import { TriangleAlert } from 'lucide-react'
import { useState } from 'react'

import { getTimelineOptions } from '@/api/@tanstack/react-query.gen'
import type { TimelineWarning } from '@/api/types.gen'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

// The "N things to check" button at the right end of the stage bar, and the flyout listing the
// itinerary's warnings. It reads the same timeline query as ItineraryTimeline (shared cache, so
// no extra request) and renders nothing while there is nothing to check.

// ----- types -----

interface TimelineWarningsProps {
    planId: string
    /** Hand a warning to the assistant; each warning gets a "Fix it" button when set. */
    onRequestFix?: (warning: TimelineWarning) => void
    /** Disables the "Fix it" buttons (e.g. while the assistant is replying). */
    fixDisabled?: boolean
}

export function TimelineWarnings({ planId, onRequestFix, fixDisabled }: TimelineWarningsProps) {
    // ----- state -----
    const timelineQuery = useQuery(getTimelineOptions({ path: { plan_id: planId } }))
    const [open, setOpen] = useState(false)

    const warnings = timelineQuery.data?.warnings ?? []
    const count = warnings.length
    const summary = `${String(count)} thing${count === 1 ? '' : 's'} to check`

    // ----- event handlers -----
    function handleFix(warning: TimelineWarning) {
        // The fix goes to the chat, so get the flyout out of the way.
        setOpen(false)
        onRequestFix?.(warning)
    }

    // ----- markup -----
    if (count === 0) return null

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button size="sm" variant="ghost" className="text-warning shrink-0">
                    <TriangleAlert aria-hidden />
                    {summary}
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" aria-label="Things to check" className="w-96 p-3">
                <p className="mb-2 text-xs font-medium">{summary}</p>
                <ul className="text-muted-foreground flex list-disc flex-col gap-1 pl-5 text-xs">
                    {warnings.map((warning, index) => (
                        // Warnings have no id; their order is stable for a given version.
                        <li key={`${warning.kind}-${String(index)}`}>
                            {/* The row is a flex wrapper inside the li, so the li keeps its
                                list marker. */}
                            <div className="flex items-center justify-between gap-2">
                                <span>{warning.message}</span>
                                {onRequestFix && (
                                    <Button
                                        size="xs"
                                        variant="ghost"
                                        className="shrink-0"
                                        aria-label={`Fix it: ${warning.message}`}
                                        disabled={fixDisabled}
                                        onClick={() => {
                                            handleFix(warning)
                                        }}
                                    >
                                        Fix it
                                    </Button>
                                )}
                            </div>
                        </li>
                    ))}
                </ul>
            </PopoverContent>
        </Popover>
    )
}
