import type { Timeline } from '@/api/types.gen'

// The "Itinerary" title bar with the day and item counts.

// ----- types -----

interface TimelineHeaderProps {
    timeline: Timeline
}

export function TimelineHeader({ timeline }: TimelineHeaderProps) {
    // ----- state -----
    // A flight spanning several days appears in several entries, so count distinct ids.
    const itemCount =
        timeline.lodging.length +
        new Set(timeline.days.flatMap((day) => day.entries.map((entry) => entry.item.id))).size
    const dayCount = timeline.days.length

    // ----- markup -----
    return (
        <div className="flex items-baseline gap-3 border-b px-5 py-2">
            <h2 className="font-heading text-lg">Itinerary</h2>
            {dayCount > 0 && (
                <p className="text-muted-foreground text-xs">
                    {dayCount} day{dayCount === 1 ? '' : 's'} · {itemCount} item
                    {itemCount === 1 ? '' : 's'}
                </p>
            )}
        </div>
    )
}
