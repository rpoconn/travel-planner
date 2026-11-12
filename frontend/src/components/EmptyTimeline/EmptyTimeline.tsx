import { IconCalendarWeek } from '@tabler/icons-react'

// Placeholder shown when the trip has no dates and nothing planned, so no day columns exist.

export function EmptyTimeline() {
    // ----- markup -----
    return (
        <div className="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
            <div className="bg-secondary flex size-12 items-center justify-center rounded-full">
                <IconCalendarWeek className="text-primary size-6" aria-hidden />
            </div>
            <p className="font-heading text-foreground text-lg">No itinerary yet</p>
            <p className="max-w-xs text-sm">
                Once the trip has dates or the assistant adds something, your days appear here as a
                timeline: flights, stays, activities, and how you get between them.
            </p>
        </div>
    )
}
