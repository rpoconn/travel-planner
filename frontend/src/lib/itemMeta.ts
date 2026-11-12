import {
    IconBed,
    IconBus,
    IconCar,
    IconCarSuv,
    IconMapPin,
    IconPlane,
    IconShip,
    IconTrain,
    IconWalk,
    type TablerIcon,
} from '@tabler/icons-react'

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
    icon: TablerIcon
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
        icon: IconPlane,
        text: 'text-flight',
        tint: 'bg-flight/12',
        border: 'border-l-flight',
    },
    lodging: {
        label: 'Stay',
        icon: IconBed,
        text: 'text-lodging',
        tint: 'bg-lodging/18',
        border: 'border-l-lodging',
    },
    activity: {
        label: 'Activity',
        icon: IconMapPin,
        text: 'text-activity',
        tint: 'bg-activity/12',
        border: 'border-l-activity',
    },
    transfer: {
        label: 'Transfer',
        icon: IconTrain,
        text: 'text-transfer',
        tint: 'bg-transfer/12',
        border: 'border-l-transfer',
    },
}

// ----- transfer modes -----

export const TRANSFER_MODE_META: Record<TransferMode, { label: string; icon: TablerIcon }> = {
    car_rental: { label: 'Car rental', icon: IconCar },
    car_service: { label: 'Car service', icon: IconCar },
    own_car: { label: 'Your car', icon: IconCar },
    taxi: { label: 'Taxi', icon: IconCar },
    rideshare: { label: 'Rideshare', icon: IconCar },
    train: { label: 'Train', icon: IconTrain },
    bus: { label: 'Bus', icon: IconBus },
    ferry: { label: 'Ferry', icon: IconShip },
    shuttle: { label: 'Shuttle', icon: IconCarSuv },
    walk: { label: 'Walk', icon: IconWalk },
    metro: { label: 'Metro', icon: IconTrain },
}

// ----- labels -----

/** "food" -> "Food", "car_rental" -> "Car rental". */
export function humanize(value: string): string {
    const words = value.replaceAll('_', ' ')
    return words.charAt(0).toUpperCase() + words.slice(1)
}
