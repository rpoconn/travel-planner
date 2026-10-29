import { ExternalLink } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import type { Place } from '@/api/types.gen'
import { Badge } from '@/components/ui/badge'
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from '@/components/ui/sheet'
import { formatMoney, placeLabel, placeLocality } from '@/lib/format'
import { humanize, ITEM_TYPE_META, TRANSFER_MODE_META, type ItineraryItem } from '@/lib/itemMeta'
import { dayOffset, formatDuration, formatWallDate, formatWallTime } from '@/lib/timeline'
import { cn } from '@/lib/utils'

// Side sheet with every field of one itinerary item. Read-only: changes go through the chat.

// ----- types -----

interface ItemDetailSheetProps {
    /** The item to show; null closes the sheet. */
    item: ItineraryItem | null
    onClose: () => void
}

interface Field {
    label: string
    value: ReactNode
}

// ----- field lists -----

function placeValue(place: Place): ReactNode {
    const locality = placeLocality(place)
    return (
        <>
            {placeLabel(place)}
            {place.address && <span className="text-muted-foreground block">{place.address}</span>}
            {locality && <span className="text-muted-foreground block">{locality}</span>}
        </>
    )
}

function whenValue(item: ItineraryItem): string {
    const start = `${formatWallDate(item.start)}, ${formatWallTime(item.start)}`
    // Same local day: only repeat the time.
    const end =
        dayOffset(item.start, item.end) === 0
            ? formatWallTime(item.end)
            : `${formatWallDate(item.end)}, ${formatWallTime(item.end)}`
    return `${start} → ${end}`
}

function timezoneValue(item: ItineraryItem): string {
    const endZone = item.end_timezone ?? item.timezone
    return endZone === item.timezone ? item.timezone : `${item.timezone} → ${endZone}`
}

/** Fields specific to each item type, in display order. */
function typeFields(item: ItineraryItem): Field[] {
    switch (item.type) {
        case 'flight':
            return [
                {
                    label: 'Flight',
                    value: [item.carrier, item.flight_number].filter(Boolean).join(' '),
                },
                { label: 'From', value: placeValue(item.from_place) },
                { label: 'To', value: placeValue(item.to_place) },
                { label: 'Duration', value: formatDuration(item.start, item.end) },
                {
                    label: 'Layovers',
                    value: item.layovers.length
                        ? item.layovers.map(placeLabel).join(', ')
                        : 'Nonstop',
                },
                { label: 'Cabin', value: humanize(item.cabin) },
                { label: 'Direction', value: humanize(item.direction) },
            ]
        case 'lodging': {
            const nights = Math.max(0, dayOffset(item.start, item.end))
            return [
                { label: 'Where', value: placeValue(item.place) },
                { label: 'Type', value: humanize(item.kind) },
                { label: 'Nights', value: String(nights) },
                { label: 'Room', value: item.room_type },
                {
                    label: 'Nightly rate',
                    value: item.nightly_rate && formatMoney(item.nightly_rate),
                },
            ]
        }
        case 'activity':
            return [
                { label: 'Where', value: placeValue(item.place) },
                { label: 'Category', value: humanize(item.category) },
                { label: 'Booking', value: item.booking_required ? 'Required' : 'Not needed' },
            ]
        case 'transfer':
            return [
                { label: 'Mode', value: TRANSFER_MODE_META[item.mode].label },
                { label: 'Provider', value: item.provider },
                { label: 'From', value: placeValue(item.from_place) },
                { label: 'To', value: placeValue(item.to_place) },
                { label: 'Duration', value: formatDuration(item.start, item.end) },
                {
                    label: 'Pick-up',
                    value: item.pickup_location && placeValue(item.pickup_location),
                },
                {
                    label: 'Drop-off',
                    value: item.dropoff_location && placeValue(item.dropoff_location),
                },
            ]
    }
}

function commonFields(item: ItineraryItem): Field[] {
    return [
        { label: 'Cost', value: item.cost && formatMoney(item.cost) },
        { label: 'Booking ref', value: item.booking_ref },
        {
            label: 'Source',
            value: item.source_url && (
                <a
                    href={item.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary inline-flex items-center gap-1 break-all underline-offset-4 hover:underline"
                >
                    {sourceLabel(item.source_url)}
                    <ExternalLink className="size-3.5 shrink-0" aria-hidden />
                </a>
            ),
        },
        { label: 'Notes', value: item.notes || null },
        {
            label: 'Added by',
            value: item.created_by === 'assistant' ? 'Assistant' : 'You',
        },
    ]
}

/** Host name of a link ("www.booking.com"), or the raw text if it doesn't parse. */
function sourceLabel(url: string): string {
    try {
        return new URL(url).host || url
    } catch {
        return url
    }
}

export function ItemDetailSheet({ item, onClose }: ItemDetailSheetProps) {
    // ----- state -----
    // Keep showing the last item while the sheet animates closed (item is already null then).
    const [lastItem, setLastItem] = useState(item)
    if (item !== null && item !== lastItem) setLastItem(item)
    const shown = item ?? lastItem

    // ----- markup -----
    return (
        <Sheet
            open={item !== null}
            onOpenChange={(open) => {
                if (!open) onClose()
            }}
        >
            <SheetContent className="w-full overflow-y-auto sm:max-w-md">
                {shown && <SheetBody item={shown} />}
            </SheetContent>
        </Sheet>
    )
}

// ----- subcomponents -----

function SheetBody({ item }: { item: ItineraryItem }) {
    const meta = ITEM_TYPE_META[item.type]
    const Icon = meta.icon
    // Drop empty optional fields rather than showing "—" rows. The dates are in the header.
    const fields = [
        { label: 'Time zone', value: timezoneValue(item) },
        ...typeFields(item),
        ...commonFields(item),
    ].filter((field) => field.value !== null && field.value !== undefined && field.value !== '')

    return (
        <>
            <SheetHeader className={cn('border-b pr-12', meta.tint)}>
                <div className="flex items-center gap-2">
                    <span
                        className={cn(
                            'bg-background inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium',
                            meta.text,
                        )}
                    >
                        <Icon className="size-3.5" aria-hidden />
                        {meta.label}
                    </span>
                    <Badge
                        variant={
                            item.status === 'cancelled'
                                ? 'destructive'
                                : item.status === 'confirmed'
                                  ? 'default'
                                  : 'outline'
                        }
                    >
                        {humanize(item.status)}
                    </Badge>
                </div>
                <SheetTitle className="font-heading text-2xl leading-tight">
                    {item.title}
                </SheetTitle>
                <SheetDescription>{whenValue(item)}</SheetDescription>
            </SheetHeader>

            <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-3 px-4 pb-6">
                {fields.map((field) => (
                    <div key={field.label} className="contents">
                        <dt className="text-muted-foreground text-sm">{field.label}</dt>
                        <dd className="text-sm">{field.value}</dd>
                    </div>
                ))}
            </dl>
        </>
    )
}
