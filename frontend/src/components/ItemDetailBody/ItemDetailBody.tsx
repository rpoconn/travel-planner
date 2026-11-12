import { Badge, Drawer } from '@mantine/core'
import { clsx } from 'clsx'
import { IconExternalLink } from '@tabler/icons-react'

import type { Place } from '@/api/types.gen'
import { formatCost, formatMoney, placeLabel, placeLocality } from '@/lib/format'
import { humanize, ITEM_TYPE_META, TRANSFER_MODE_META, type ItineraryItem } from '@/lib/itemMeta'
import { dayOffset, formatDuration, formatWallDate, formatWallTime } from '@/lib/timeline'

// The header and field list inside ItemDetailSheet. Read-only.

// ----- types -----

interface ItemDetailBodyProps {
    item: ItineraryItem
}

/** Plain text, a place (drawn with address and locality), or an outbound link. Empty values are dropped. */
type FieldValue = string | { place: Place } | { link: string } | null | undefined

interface Field {
    label: string
    value: FieldValue
}

// ----- helpers -----

/** A place field carries data; the body's markup decides how to draw it. */
function placeValue(place: Place): FieldValue {
    return { place }
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
        { label: 'Cost', value: item.cost && formatCost(item.cost, { verbose: true }) },
        { label: 'Booking ref', value: item.booking_ref },
        {
            label: 'Source',
            value: item.source_url ? { link: item.source_url } : null,
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

export function ItemDetailBody({ item }: ItemDetailBodyProps) {
    // ----- state -----
    const meta = ITEM_TYPE_META[item.type]
    const Icon = meta.icon
    // Drop empty optional fields rather than showing "—" rows. The dates are in the header.
    const fields: { label: string; value: NonNullable<FieldValue> }[] = []
    for (const field of [
        { label: 'Time zone', value: timezoneValue(item) },
        ...typeFields(item),
        ...commonFields(item),
    ]) {
        if (field.value !== null && field.value !== undefined && field.value !== '') {
            fields.push({ label: field.label, value: field.value })
        }
    }

    // ----- markup -----
    return (
        <>
            {/* The drawer's title, so it names the dialog; the close button sits in the
                top-right corner, over the header's right padding. */}
            <div className={clsx('flex flex-col gap-1.5 border-b p-4 pr-12', meta.tint)}>
                <div className="flex items-center gap-2">
                    <span
                        className={clsx(
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
                                ? 'light'
                                : item.status === 'confirmed'
                                  ? 'filled'
                                  : 'outline'
                        }
                        color={item.status === 'cancelled' ? 'red' : undefined}
                    >
                        {humanize(item.status)}
                    </Badge>
                </div>
                <Drawer.Title className="font-heading text-2xl leading-tight">
                    {item.title}
                </Drawer.Title>
                <p className="text-muted-foreground text-sm">{whenValue(item)}</p>
            </div>

            <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-3 px-4 pt-4 pb-6">
                {fields.map((field) => (
                    <div key={field.label} className="contents">
                        <dt className="text-muted-foreground text-sm">{field.label}</dt>
                        <dd className="text-sm">
                            {typeof field.value === 'string' ? (
                                field.value
                            ) : 'place' in field.value ? (
                                <>
                                    {placeLabel(field.value.place)}
                                    {field.value.place.address && (
                                        <span className="text-muted-foreground block">
                                            {field.value.place.address}
                                        </span>
                                    )}
                                    {placeLocality(field.value.place) && (
                                        <span className="text-muted-foreground block">
                                            {placeLocality(field.value.place)}
                                        </span>
                                    )}
                                </>
                            ) : (
                                <a
                                    href={field.value.link}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-primary inline-flex items-center gap-1 break-all underline-offset-4 hover:underline"
                                >
                                    {sourceLabel(field.value.link)}
                                    <IconExternalLink className="size-3.5 shrink-0" aria-hidden />
                                </a>
                            )}
                        </dd>
                    </div>
                ))}
            </dl>
        </>
    )
}
