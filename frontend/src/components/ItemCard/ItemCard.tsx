import { Plane, PlaneLanding, PlaneTakeoff, TriangleAlert } from 'lucide-react'

import type { TimelineEntry, TimelineWarning } from '@/api/types.gen'
import { Badge } from '@/components/ui/badge'
import { formatCost } from '@/lib/format'
import { humanize, ITEM_TYPE_META, type ItineraryItem } from '@/lib/itemMeta'
import { entryTimeLabel, flightLeg, formatDuration, formatWallDate } from '@/lib/timeline'
import { cn } from '@/lib/utils'

// A flight or activity card in a day column (transfers render as TransferConnector, lodging
// as LodgingBar). Click opens the detail sheet. A flight that lands on a later local day shows
// in full on its departure day and as a compact continuation (in flight / arriving) after.

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

/** Secondary line for a flight's continuation card (a later day of the same flight). */
function continuationLine(item: TimelineEntry['item'], arriving: boolean): string {
    if (item.type !== 'flight') return ''
    const from = item.from_place.iata ?? item.from_place.name
    const to = item.to_place.iata ?? item.to_place.name
    // The departure date tells the traveller which card this one continues.
    const departed = `departed ${formatWallDate(item.start)}`
    return arriving ? `Lands at ${to} · from ${from}, ${departed}` : `${from} → ${to}, ${departed}`
}

export function ItemCard({ entry, warnings = [], onSelect }: ItemCardProps) {
    // ----- state -----
    const { item } = entry
    const meta = ITEM_TYPE_META[item.type]
    const Icon = meta.icon
    const cancelled = item.status === 'cancelled'
    const direction = item.type === 'flight' ? item.direction : null
    const leg = item.type === 'flight' ? flightLeg(entry) : 'whole'

    // ----- markup -----
    if (leg === 'arrival' || leg === 'in_flight') {
        return (
            <ContinuationCard
                entry={entry}
                arriving={leg === 'arrival'}
                warnings={warnings}
                onSelect={onSelect}
            />
        )
    }
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

// ----- subcomponents -----

interface ContinuationCardProps {
    entry: TimelineEntry
    arriving: boolean
    warnings: readonly TimelineWarning[]
    onSelect: (item: ItineraryItem) => void
}

/**
 * A later day of a flight that started on an earlier day. Deliberately quieter than the
 * departure card (no direction badge, status or cost) so one flight doesn't read as two.
 */
function ContinuationCard({ entry, arriving, warnings, onSelect }: ContinuationCardProps) {
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
                item.status === 'cancelled' && 'opacity-60',
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
