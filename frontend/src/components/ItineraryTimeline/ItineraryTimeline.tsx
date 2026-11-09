import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getTimelineOptions } from '@/api/@tanstack/react-query.gen'
import { EmptyTimeline } from '@/components/EmptyTimeline/EmptyTimeline'
import { ItemDetailSheet } from '@/components/ItemDetailSheet/ItemDetailSheet'
import { TimelineGrid } from '@/components/TimelineGrid/TimelineGrid'
import { TimelineHeader } from '@/components/TimelineHeader/TimelineHeader'
import { errorMessage } from '@/lib/apiError'
import type { ItineraryItem } from '@/lib/itemMeta'
import { findTimelineItem } from '@/lib/timeline'

// The itinerary panel: day columns left to right (outbound flight on the first day,
// return on the last) and lodging bars spanning the nights. The layout comes from the server's
// timeline view model; plan_updated events refetch it. Clicking an item opens its details, with
// a button to remove it (the planner runs the removal as a turn in the chat). The planner's issues
// live in the stage bar (IssuesPanel); per-day and per-item warnings still mark the cards here.

// ----- types -----

interface ItineraryTimelineProps {
    planId: string
    /** Takes an item off the trip; the detail sheet has no Remove button without it. */
    onRemoveItem?: (item: ItineraryItem) => void
    /** While the planner is busy with another turn. */
    removeDisabled?: boolean
}

export function ItineraryTimeline({
    planId,
    onRemoveItem,
    removeDisabled = false,
}: ItineraryTimelineProps) {
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

    /** Close the sheet, so the chat (where the planner asks what's next) is in view. */
    function handleRemove(item: ItineraryItem) {
        setSelectedId(null)
        onRemoveItem?.(item)
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
            <TimelineHeader timeline={data} />
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
                onRemove={onRemoveItem ? handleRemove : undefined}
                removeDisabled={removeDisabled}
            />
        </div>
    )
}
