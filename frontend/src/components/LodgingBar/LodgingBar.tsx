import { TriangleAlert } from 'lucide-react'
import type { CSSProperties } from 'react'

import type { LodgingItem, TimelineWarning } from '@/api/types.gen'
import { ITEM_TYPE_META } from '@/lib/itemMeta'
import { dayOffset, formatWallTime } from '@/lib/timeline'
import { cn } from '@/lib/utils'

// A stay drawn as one continuous bar across the day columns it covers. ItineraryTimeline
// positions it on the grid (see layoutLodging in lib/timeline.ts) through `style`.

// ----- types -----

interface LodgingBarProps {
    item: LodgingItem
    warnings?: readonly TimelineWarning[]
    style?: CSSProperties
    onSelect: (item: LodgingItem) => void
}

export function LodgingBar({ item, warnings = [], style, onSelect }: LodgingBarProps) {
    // ----- state -----
    const Icon = ITEM_TYPE_META.lodging.icon
    const nights = Math.max(0, dayOffset(item.start, item.end))
    const nightsLabel = `${String(nights)} night${nights === 1 ? '' : 's'}`

    // ----- markup -----
    return (
        <button
            type="button"
            data-item-type="lodging"
            style={style}
            aria-label={`${item.title}, ${nightsLabel}`}
            title={`Check-in ${formatWallTime(item.start)} · Check-out ${formatWallTime(item.end)}`}
            className={cn(
                'border-lodging/60 bg-lodging/18 hover:bg-lodging/28 mx-1 my-1 flex min-w-0 items-center gap-1.5 rounded-full border px-3 text-left text-xs transition-colors',
                item.status === 'proposed' && 'border-dashed',
                item.status === 'cancelled' && 'line-through opacity-60',
            )}
            onClick={() => {
                onSelect(item)
            }}
        >
            <Icon className="text-lodging size-3.5 shrink-0" aria-hidden />
            <span className="truncate font-medium">{item.title}</span>
            <span className="text-muted-foreground shrink-0">· {nightsLabel}</span>
            {warnings.length > 0 && (
                <TriangleAlert className="text-warning ml-auto size-3.5 shrink-0" aria-hidden />
            )}
        </button>
    )
}
