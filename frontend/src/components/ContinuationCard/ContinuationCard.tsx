import { Plane, PlaneLanding, TriangleAlert } from 'lucide-react'

import type { TimelineEntry, TimelineWarning } from '@/api/types.gen'
import { ITEM_TYPE_META, type ItineraryItem } from '@/lib/itemMeta'
import { entryTimeLabel, formatWallDate } from '@/lib/timeline'
import { cn } from '@/lib/utils'

// A later day of a flight that started on an earlier day. Deliberately quieter than the
// departure card (no direction badge, status or cost) so one flight doesn't read as two.

// ----- types -----

interface ContinuationCardProps {
    entry: TimelineEntry
    arriving: boolean
    warnings: readonly TimelineWarning[]
    onSelect: (item: ItineraryItem) => void
}

// ----- helpers -----

/** Secondary line for a flight's continuation card (a later day of the same flight). */
function continuationLine(item: TimelineEntry['item'], arriving: boolean): string {
    if (item.type !== 'flight') return ''
    const from = item.from_place.iata ?? item.from_place.name
    const to = item.to_place.iata ?? item.to_place.name
    // The departure date tells the traveller which card this one continues.
    const departed = `departed ${formatWallDate(item.start)}`
    return arriving ? `Lands at ${to} · from ${from}, ${departed}` : `${from} → ${to}, ${departed}`
}

export function ContinuationCard({ entry, arriving, warnings, onSelect }: ContinuationCardProps) {
    // ----- state -----
    const { item } = entry
    const meta = ITEM_TYPE_META[item.type]
    const LegIcon = arriving ? PlaneLanding : Plane

    // ----- markup -----
    return (
        <button
            type="button"
            data-item-type={item.type}
            data-continuation={arriving ? 'arrival' : 'in_flight'}
            className={cn(
                'bg-muted/40 w-full rounded-xl border border-l-4 border-dashed [border-left-style:solid] px-3 py-2 text-left transition-shadow hover:shadow-md',
                meta.border,
            )}
            onClick={() => {
                onSelect(item)
            }}
        >
            <div className="flex items-center gap-1.5 text-xs">
                <LegIcon className={cn('size-3.5', meta.text)} aria-hidden />
                <span className="font-medium">{entryTimeLabel(entry)}</span>
                {warnings.length > 0 && (
                    <span
                        className="text-warning ml-auto inline-flex items-center gap-1"
                        title={warnings.map((warning) => warning.message).join('\n')}
                    >
                        <TriangleAlert className="size-3.5" aria-hidden />
                        <span className="sr-only">
                            {warnings.map((warning) => warning.message).join('. ')}
                        </span>
                    </span>
                )}
            </div>
            <p className="text-muted-foreground mt-0.5 truncate text-xs">
                {continuationLine(item, arriving)}
            </p>
            <span className="sr-only">{item.title}</span>
        </button>
    )
}
