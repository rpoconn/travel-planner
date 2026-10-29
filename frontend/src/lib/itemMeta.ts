import {
    BedDouble,
    Bus,
    Car,
    CarTaxiFront,
    Footprints,
    MapPin,
    Plane,
    Ship,
    TrainFront,
    TramFront,
    Van,
    type LucideIcon,
} from 'lucide-react'

import type { ActivityItem, FlightItem, LodgingItem, TransferItem } from '@/api/types.gen'

// Display metadata for itinerary items: label, icon and the Tailwind classes for each type's
// color token (defined in index.css). Class names are spelled out in full so Tailwind's
// scanner can find them.

// ----- types -----

export type ItineraryItem = FlightItem | LodgingItem | ActivityItem | TransferItem
export type ItemType = ItineraryItem['type']
export type TransferMode = TransferItem['mode']

interface ItemTypeMeta {
    label: string
    icon: LucideIcon
    /** Icon / accent text color. */
    text: string
    /** Soft tinted background. */
    tint: string
    /** Left-edge accent border color for cards. */
    border: string
}

// ----- item types -----

export const ITEM_TYPE_META: Record<ItemType, ItemTypeMeta> = {
    flight: {
        label: 'Flight',
        icon: Plane,
        text: 'text-flight',
        tint: 'bg-flight/12',
        border: 'border-l-flight',
    },
    lodging: {
        label: 'Stay',
        icon: BedDouble,
        text: 'text-lodging',
        tint: 'bg-lodging/18',
        border: 'border-l-lodging',
    },
    activity: {
        label: 'Activity',
        icon: MapPin,
        text: 'text-activity',
        tint: 'bg-activity/12',
        border: 'border-l-activity',
    },
    transfer: {
        label: 'Transfer',
        icon: TrainFront,
        text: 'text-transfer',
        tint: 'bg-transfer/12',
        border: 'border-l-transfer',
    },
}

// ----- transfer modes -----

export const TRANSFER_MODE_META: Record<TransferMode, { label: string; icon: LucideIcon }> = {
    car_rental: { label: 'Car rental', icon: Car },
    taxi: { label: 'Taxi', icon: CarTaxiFront },
    rideshare: { label: 'Rideshare', icon: Car },
    train: { label: 'Train', icon: TrainFront },
    bus: { label: 'Bus', icon: Bus },
    ferry: { label: 'Ferry', icon: Ship },
    shuttle: { label: 'Shuttle', icon: Van },
    walk: { label: 'Walk', icon: Footprints },
    metro: { label: 'Metro', icon: TramFront },
}

// ----- labels -----

/** "food" -> "Food", "car_rental" -> "Car rental". */
export function humanize(value: string): string {
    const words = value.replaceAll('_', ' ')
    return words.charAt(0).toUpperCase() + words.slice(1)
}
