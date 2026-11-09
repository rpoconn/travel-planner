import { useQuery } from '@tanstack/react-query'

import { getTimelineOptions } from '@/api/@tanstack/react-query.gen'
import type { TravelPlan } from '@/api/types.gen'

import { tripSummaryLine } from './TripSummaryService'

// The selected trip in the page's top bar: its title, then where and when it is and how much is
// planned ("London · Feb 9 – Feb 17 · 9 days · 21 items"). One line, so neither the itinerary nor
// the chat needs a title bar of its own.

// ----- types -----

interface TripSummaryProps {
    plan: TravelPlan
}

export function TripSummary({ plan }: TripSummaryProps) {
    // ----- state -----
    // The itinerary's query, so this reads its cache entry rather than asking again.
    const timelineQuery = useQuery(getTimelineOptions({ path: { plan_id: plan.id } }))
    const details = tripSummaryLine(plan, timelineQuery.data)

    // ----- markup -----
    return (
        <div className="flex min-w-0 items-baseline gap-2.5">
            <h1 className="font-heading truncate text-lg">{plan.title}</h1>
            {/* The large shrink factor gives up the details' width before the title's. */}
            {details && (
                <p className="text-muted-foreground min-w-0 shrink-[100] truncate text-xs">
                    {details}
                </p>
            )}
        </div>
    )
}
