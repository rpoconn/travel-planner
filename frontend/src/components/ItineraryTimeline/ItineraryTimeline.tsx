import { useQuery } from '@tanstack/react-query'
import { CalendarRange, TriangleAlert } from 'lucide-react'
import { useState } from 'react'

import { getTimelineOptions } from '@/api/@tanstack/react-query.gen'
import type { Timeline, TimelineWarning } from '@/api/types.gen'
import { DayColumn } from '@/components/DayColumn/DayColumn'
import { ItemDetailSheet } from '@/components/ItemDetailSheet/ItemDetailSheet'
import { LodgingBar } from '@/components/LodgingBar/LodgingBar'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import type { ItineraryItem } from '@/lib/itemMeta'
import {
    dayGridColumn,
    findTimelineItem,
    HALF_COLUMNS_PER_DAY,
    layoutLodging,
    warningsByDate,
    warningsByItem,
} from '@/lib/timeline'

// The read-only itinerary panel: day columns left to right (outbound flight on the first day,
// return on the last), lodging bars spanning the nights, and a warnings summary. The layout
// comes from the server's timeline view model; plan_updated events refetch it.

// ----- types -----

interface ItineraryTimelineProps {
    planId: string
    /** Hand a warning to the assistant; each warning gets a "Fix it" button when set. */
    onRequestFix?: (warning: TimelineWarning) => void
    /** Disables the "Fix it" buttons (e.g. while the assistant is replying). */
    fixDisabled?: boolean
}

/** Width of one half-column; a day is two of these. */
const HALF_COLUMN_WIDTH = '7.5rem'
const LODGING_LANE_HEIGHT = '2.25rem'

export function ItineraryTimeline({ planId, onRequestFix, fixDisabled }: ItineraryTimelineProps) {
    // ----- state -----
    const timelineQuery = useQuery(getTimelineOptions({ path: { plan_id: planId } }))
    // Store the id, not the item, so the sheet shows fresh data after the assistant edits it
    // and closes by itself if the item is removed.
    const [selectedId, setSelectedId] = useState<string | null>(null)

    const timeline = timelineQuery.data
    const selectedItem =
        timeline && selectedId !== null ? (findTimelineItem(timeline, selectedId) ?? null) : null

    // ----- event handlers -----
    function handleSelect(item: ItineraryItem) {
        setSelectedId(item.id)
    }

    // ----- markup -----
    if (timelineQuery.isPending) {
        return <p className="text-muted-foreground p-6 text-sm">Loading itinerary…</p>
    }
    if (timelineQuery.isError) {
        return (
            <p role="alert" className="text-destructive p-6 text-sm">
                {errorMessage(timelineQuery.error, "Couldn't load the itinerary")}
            </p>
        )
    }

    const data = timelineQuery.data
    return (
        <div className="flex h-full flex-col">
            <TimelineHeader timeline={data} onRequestFix={onRequestFix} fixDisabled={fixDisabled} />
            {data.days.length === 0 ? (
                <EmptyTimeline />
            ) : (
                <TimelineGrid timeline={data} onSelectItem={handleSelect} />
            )}
            <ItemDetailSheet
                item={selectedItem}
                onClose={() => {
                    setSelectedId(null)
                }}
            />
        </div>
    )
}

// ----- subcomponents -----

interface TimelineHeaderProps {
    timeline: Timeline
    onRequestFix?: (warning: TimelineWarning) => void
    fixDisabled?: boolean
}

function TimelineHeader({ timeline, onRequestFix, fixDisabled }: TimelineHeaderProps) {
    const itemCount =
        timeline.lodging.length +
        new Set(timeline.days.flatMap((day) => day.entries.map((entry) => entry.item.id))).size
    const dayCount = timeline.days.length

    return (
        <div className="flex flex-col gap-2 border-b px-5 py-3">
            <div className="flex items-baseline gap-3">
                <h2 className="font-heading text-lg">Itinerary</h2>
                {dayCount > 0 && (
                    <p className="text-muted-foreground text-xs">
                        {dayCount} day{dayCount === 1 ? '' : 's'} · {itemCount} item
                        {itemCount === 1 ? '' : 's'}
                    </p>
                )}
            </div>
            {timeline.warnings.length > 0 && (
                <details className="group text-sm">
                    <summary className="text-warning inline-flex cursor-pointer items-center gap-1.5 text-xs font-medium select-none">
                        <TriangleAlert className="size-3.5" aria-hidden />
                        {timeline.warnings.length} thing
                        {timeline.warnings.length === 1 ? '' : 's'} to check
                    </summary>
                    <ul className="text-muted-foreground mt-2 flex list-disc flex-col gap-1 pl-5 text-xs">
                        {timeline.warnings.map((warning, index) => (
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
                                            aria-label={`Fix it: ${warning.message}`}
                                            disabled={fixDisabled}
                                            onClick={() => {
                                                onRequestFix(warning)
                                            }}
                                        >
                                            Fix it
                                        </Button>
                                    )}
                                </div>
                            </li>
                        ))}
                    </ul>
                </details>
            )}
        </div>
    )
}

interface TimelineGridProps {
    timeline: Timeline
    onSelectItem: (item: ItineraryItem) => void
}

function TimelineGrid({ timeline, onSelectItem }: TimelineGridProps) {
    const { placements, laneCount } = layoutLodging(timeline.lodging)
    const byDate = warningsByDate(timeline.warnings)
    const byItem = warningsByItem(timeline.warnings)
    const hasItems = laneCount > 0 || timeline.days.some((day) => day.entries.length > 0)

    // Rows: date headers, one row per lodging lane, then the cards (filling the height).
    const gridTemplateRows = [
        'auto',
        ...Array.from({ length: laneCount }, () => LODGING_LANE_HEIGHT),
        'minmax(0, 1fr)',
    ].join(' ')

    return (
        <div className="relative min-h-0 flex-1 overflow-auto" data-testid="timeline-scroll">
            <div
                className="grid min-h-full w-max"
                style={{
                    gridTemplateColumns: `repeat(${String(timeline.days.length * HALF_COLUMNS_PER_DAY)}, ${HALF_COLUMN_WIDTH})`,
                    gridTemplateRows,
                }}
            >
                {timeline.days.map((day) => (
                    <DayColumn
                        key={day.date}
                        day={day}
                        previousDay={timeline.days[day.index - 1]}
                        warnings={timeline.warnings}
                        dayWarnings={byDate.get(day.date)}
                        warningsByItem={byItem}
                        style={{ gridColumn: dayGridColumn(day.index), gridRow: '1 / -1' }}
                        onSelectItem={onSelectItem}
                    />
                ))}
                {/* Drawn after the columns so the bars paint above the column borders. */}
                {placements.map(({ span, lane, gridColumnStart, gridColumnEnd }) => (
                    <LodgingBar
                        key={span.item.id}
                        item={span.item}
                        warnings={byItem.get(span.item.id)}
                        style={{
                            gridColumn: `${String(gridColumnStart)} / ${String(gridColumnEnd)}`,
                            gridRow: lane + 2,
                        }}
                        onSelect={onSelectItem}
                    />
                ))}
            </div>
            {!hasItems && (
                <p className="bg-card text-muted-foreground absolute bottom-4 left-4 max-w-xs rounded-xl border px-4 py-3 text-sm shadow-sm">
                    Nothing planned yet. Ask the assistant for flights, a hotel, or ideas for each
                    day.
                </p>
            )}
        </div>
    )
}

function EmptyTimeline() {
    return (
        <div className="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
            <div className="bg-secondary flex size-12 items-center justify-center rounded-full">
                <CalendarRange className="text-primary size-6" aria-hidden />
            </div>
            <p className="font-heading text-foreground text-lg">No itinerary yet</p>
            <p className="max-w-xs text-sm">
                Once the trip has dates or the assistant adds something, your days appear here as a
                timeline: flights, stays, activities, and how you get between them.
            </p>
        </div>
    )
}
