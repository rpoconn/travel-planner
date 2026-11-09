import type { Timeline, TravelPlan } from '@/api/types.gen'
import { formatTripWhereWhen } from '@/lib/format'

// Pure logic for TripSummary: the one line of facts shown after the trip's title.

// ----- helpers -----

function counted(count: number, noun: string): string {
    return `${String(count)} ${noun}${count === 1 ? '' : 's'}`
}

// ----- text -----

/**
 * How many items are on the timeline. A flight spanning several days appears in each of those
 * days' entries, so ids are counted once.
 */
export function timelineItemCount(timeline: Timeline): number {
    const onDays = new Set(
        timeline.days.flatMap((day) => day.entries.map((entry) => entry.item.id)),
    )
    return timeline.lodging.length + onDays.size
}

/** "9 days · 21 items", or null while the trip has no days to lay out. */
export function timelineCounts(timeline: Timeline): string | null {
    const dayCount = timeline.days.length
    if (dayCount === 0) return null
    return `${counted(dayCount, 'day')} · ${counted(timelineItemCount(timeline), 'item')}`
}

/**
 * "London · Feb 9 – Feb 17 · 9 days · 21 items": where and when the trip is, then how much is
 * planned. Parts not known yet (or a timeline still loading) are left out; '' when none are.
 */
export function tripSummaryLine(plan: TravelPlan, timeline: Timeline | undefined): string {
    return [formatTripWhereWhen(plan), timeline ? timelineCounts(timeline) : null]
        .filter(Boolean)
        .join(' · ')
}
