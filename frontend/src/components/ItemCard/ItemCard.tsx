import { PlaneLanding, PlaneTakeoff, TriangleAlert } from 'lucide-react'

import type { TimelineEntry, TimelineWarning } from '@/api/types.gen'
import { Badge } from '@/components/ui/badge'
import { formatCost } from '@/lib/format'
import { humanize, ITEM_TYPE_META, type ItineraryItem } from '@/lib/itemMeta'
import { entryTimeLabel, formatDuration } from '@/lib/timeline'
import { cn } from '@/lib/utils'

// A flight or activity card in a day column (transfers render as TransferConnector, lodging
// as LodgingBar). Click opens the detail sheet.

// ----- types -----

interface ItemCardProps {
    entry: TimelineEntry
    warnings?: readonly TimelineWarning[]
    onSelect: (item: ItineraryItem) => void
}

/** Secondary line under the title, specific to each item type. */
function detailLine(item: TimelineEntry['item']): string | null {
    switch (item.type) {
        case 'flight': {
            const from = item.from_place.iata ?? item.from_place.name
            const to = item.to_place.iata ?? item.to_place.name
            const flight = [item.carrier, item.flight_number].filter(Boolean).join(' ')
            return `${from} → ${to} · ${flight} · ${formatDuration(item.start, item.end)}`
        }
        case 'activity':
            return `${humanize(item.category)} · ${item.place.name}`
        case 'transfer':
            return `${item.from_place.name} → ${item.to_place.name}`
    }
}

export function ItemCard({ entry, warnings = [], onSelect }: ItemCardProps) {
    // ----- state -----
    const { item } = entry
    const meta = ITEM_TYPE_META[item.type]
    const Icon = meta.icon
    const cancelled = item.status === 'cancelled'
    const direction = item.type === 'flight' ? item.direction : null

    // ----- markup -----
    return (
        <button
            type="button"
            data-item-type={item.type}
            className={cn(
                'bg-card w-full rounded-xl border border-l-4 p-3 text-left shadow-xs transition-shadow hover:shadow-md',
                meta.border,
                // Proposed (not yet confirmed) items get a dashed outline; the accent edge stays solid.
                item.status === 'proposed' && 'border-dashed [border-left-style:solid]',
                cancelled && 'opacity-60',
            )}
            onClick={() => {
                onSelect(item)
            }}
        >
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs">
                <Icon className={cn('size-3.5', meta.text)} aria-hidden />
                <span className="text-muted-foreground">{entryTimeLabel(entry)}</span>
                {direction === 'outbound' && (
                    <span className="text-flight ml-auto inline-flex items-center gap-1 font-medium">
                        <PlaneTakeoff className="size-3.5" aria-hidden />
                        Outbound
                    </span>
                )}
                {direction === 'return' && (
                    <span className="text-flight ml-auto inline-flex items-center gap-1 font-medium">
                        <PlaneLanding className="size-3.5" aria-hidden />
                        Return
                    </span>
                )}
            </div>

            <p className={cn('mt-1 line-clamp-2 text-sm font-medium', cancelled && 'line-through')}>
                {item.title}
            </p>
            {detailLine(item) && (
                <p className="text-muted-foreground mt-0.5 truncate text-xs">{detailLine(item)}</p>
            )}

            <div className="mt-2 flex flex-wrap items-center gap-1.5 empty:hidden">
                {item.status !== 'proposed' && (
                    <Badge variant={cancelled ? 'destructive' : 'secondary'}>
                        {humanize(item.status)}
                    </Badge>
                )}
                {item.cost && (
                    <span className="text-muted-foreground text-xs">{formatCost(item.cost)}</span>
                )}
                {warnings.length > 0 && (
                    <span
                        className="text-warning ml-auto inline-flex items-center gap-1 text-xs"
                        title={warnings.map((warning) => warning.message).join('\n')}
                    >
                        <TriangleAlert className="size-3.5" aria-hidden />
                        <span className="sr-only">
                            {warnings.map((warning) => warning.message).join('. ')}
                        </span>
                    </span>
                )}
            </div>
        </button>
    )
}
