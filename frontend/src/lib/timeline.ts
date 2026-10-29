import type { LodgingSpan, Timeline, TimelineEntry, TimelineWarning } from '@/api/types.gen'
import type { ItineraryItem } from '@/lib/itemMeta'

// Pure helpers behind the itinerary timeline: reading item times as local wall-clock time,
// labels for cards, and the grid math that places lodging bars across day columns.

// ----- wall-clock times -----
// Item times arrive as ISO strings already offset to the place's own timezone, e.g. a Tokyo
// check-in is "2027-05-01T15:00:00+09:00". Reading the digits directly keeps that local time;
// `new Date(iso)` would shift it into the *viewer's* timezone, which is wrong for a trip.

export interface WallClock {
    year: number
    month: number
    day: number
    hour: number
    minute: number
}

const ISO_WALL_CLOCK = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/

export function parseWallClock(iso: string): WallClock {
    const match = ISO_WALL_CLOCK.exec(iso)
    if (!match) throw new Error(`Not an ISO date/time: ${iso}`)
    const [, year, month, day, hour = '0', minute = '0'] = match
    return {
        year: Number(year),
        month: Number(month),
        day: Number(day),
        hour: Number(hour),
        minute: Number(minute),
    }
}

/**
 * The wall-clock reading as a Date whose *UTC* fields hold it. Formatting with
 * `timeZone: 'UTC'` then prints exactly the local time of the place.
 */
function wallClockAsUtc({ year, month, day, hour, minute }: WallClock): Date {
    return new Date(Date.UTC(year, month - 1, day, hour, minute))
}

const TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'UTC',
})
const DATE_FORMAT = new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
})
const WEEKDAY_FORMAT = new Intl.DateTimeFormat(undefined, { weekday: 'short', timeZone: 'UTC' })
const MONTH_DAY_FORMAT = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
})

/** "3:00 PM" (locale-dependent) in the place's local time. */
export function formatWallTime(iso: string): string {
    return TIME_FORMAT.format(wallClockAsUtc(parseWallClock(iso)))
}

/** "Sat, May 1" in the place's local time. */
export function formatWallDate(iso: string): string {
    return DATE_FORMAT.format(wallClockAsUtc(parseWallClock(iso)))
}

/** Header for a timeline day (`YYYY-MM-DD`): `{ weekday: "Sat", date: "May 1" }`. */
export function formatDayHeader(isoDate: string): { weekday: string; date: string } {
    const utc = wallClockAsUtc(parseWallClock(isoDate))
    return { weekday: WEEKDAY_FORMAT.format(utc), date: MONTH_DAY_FORMAT.format(utc) }
}

/**
 * Calendar days between two local readings: 1 for a flight landing "the next day" in the
 * arrival city's own time, -1 for an eastbound date-line crossing that lands "yesterday".
 */
export function dayOffset(startIso: string, endIso: string): number {
    const start = parseWallClock(startIso)
    const end = parseWallClock(endIso)
    const startDay = Date.UTC(start.year, start.month - 1, start.day)
    const endDay = Date.UTC(end.year, end.month - 1, end.day)
    return Math.round((endDay - startDay) / 86_400_000)
}

/** Elapsed time between two instants ("11h 30m", "45m"); unlike labels, this uses offsets. */
export function formatDuration(startIso: string, endIso: string): string {
    const minutes = Math.max(0, Math.round((Date.parse(endIso) - Date.parse(startIso)) / 60_000))
    const hours = Math.floor(minutes / 60)
    const rest = minutes % 60
    if (hours === 0) return `${String(rest)}m`
    return rest === 0 ? `${String(hours)}h` : `${String(hours)}h ${String(rest)}m`
}

/** " (+1)" / " (−1)" suffix for an arrival on another local day, else "". */
export function dayOffsetSuffix(startIso: string, endIso: string): string {
    const offset = dayOffset(startIso, endIso)
    if (offset === 0) return ''
    return offset > 0 ? ` (+${String(offset)})` : ` (−${String(-offset)})`
}

/**
 * Which part of a multi-day item one day's card shows. A flight that lands on a later local
 * day (long-haul, or crossing time zones eastward) is drawn on each day it touches; only the
 * departure card is "the flight", the others are continuations of it.
 */
export type FlightLeg = 'whole' | 'departure' | 'in_flight' | 'arrival'

export function flightLeg(entry: TimelineEntry): FlightLeg {
    const { continues_from_previous_day: fromPrevious, continues_to_next_day: toNext } = entry
    if (fromPrevious && toNext) return 'in_flight'
    if (fromPrevious) return 'arrival'
    if (toNext) return 'departure'
    return 'whole'
}

/**
 * Time label for a card on one day. Items that cross midnight show on every day they touch,
 * so each day only shows the part that's relevant to it.
 */
export function entryTimeLabel(entry: TimelineEntry): string {
    const { start, end } = entry.item
    if (entry.item.type === 'flight') {
        // Both ends are local times, so a flight shows its local arrival time on the departure
        // day too, flagged "(+1)" when that's on another calendar day.
        switch (flightLeg(entry)) {
            case 'departure':
                return `${formatWallTime(start)} → ${formatWallTime(end)}${dayOffsetSuffix(start, end)}`
            case 'in_flight':
                return 'In flight'
            case 'arrival':
                return `Arrives ${formatWallTime(end)}`
            case 'whole':
                break
        }
    }
    if (entry.continues_from_previous_day && entry.continues_to_next_day) return 'All day'
    if (entry.continues_from_previous_day) return `Until ${formatWallTime(end)}`
    if (entry.continues_to_next_day) return `${formatWallTime(start)} →`
    if (start === end) return formatWallTime(start)
    return `${formatWallTime(start)} – ${formatWallTime(end)}`
}

// ----- lodging layout -----
// The grid has two half-columns per day. A stay is drawn from the middle of its check-in day
// to the middle of its check-out day, so back-to-back stays meet in the middle of the
// changeover day and can share a lane instead of stacking.

export const HALF_COLUMNS_PER_DAY = 2

export interface LodgingPlacement {
    span: LodgingSpan
    /** 0-based lane (row) the bar sits in. */
    lane: number
    /** 1-based CSS grid lines on the half-column grid. */
    gridColumnStart: number
    gridColumnEnd: number
}

export interface LodgingLayout {
    placements: LodgingPlacement[]
    laneCount: number
}

export function layoutLodging(spans: readonly LodgingSpan[]): LodgingLayout {
    // Half-column offsets: the middle of day i is offset 2i + 1.
    const intervals = spans
        .map((span) => {
            const start = HALF_COLUMNS_PER_DAY * span.start_day_index + 1
            // A same-day stay still gets one half-column so it's visible.
            const end = Math.max(HALF_COLUMNS_PER_DAY * span.end_day_index + 1, start + 1)
            return { span, start, end }
        })
        .sort((a, b) => a.start - b.start || a.end - b.end)

    // Greedy interval partitioning: each bar goes in the first lane that's free by its start.
    const laneEnds: number[] = []
    const placements = intervals.map(({ span, start, end }) => {
        let lane = laneEnds.findIndex((laneEnd) => laneEnd <= start)
        if (lane === -1) {
            lane = laneEnds.length
            laneEnds.push(end)
        } else {
            laneEnds[lane] = end
        }
        return { span, lane, gridColumnStart: start + 1, gridColumnEnd: end + 1 }
    })

    return { placements, laneCount: laneEnds.length }
}

/** Grid placement of a whole day column on the half-column grid. */
export function dayGridColumn(dayIndex: number): string {
    return `${String(HALF_COLUMNS_PER_DAY * dayIndex + 1)} / span ${String(HALF_COLUMNS_PER_DAY)}`
}

// ----- warnings -----

/** Warnings grouped by the day they're about (warnings without a date are left out). */
export function warningsByDate(warnings: readonly TimelineWarning[]) {
    const byDate = new Map<string, TimelineWarning[]>()
    for (const warning of warnings) {
        if (warning.date === null) continue
        byDate.set(warning.date, [...(byDate.get(warning.date) ?? []), warning])
    }
    return byDate
}

/** Warnings that mention each item id. */
export function warningsByItem(warnings: readonly TimelineWarning[]) {
    const byItem = new Map<string, TimelineWarning[]>()
    for (const warning of warnings) {
        for (const id of warning.item_ids) {
            byItem.set(id, [...(byItem.get(id) ?? []), warning])
        }
    }
    return byItem
}

/**
 * The `missing_transfer` warning between two consecutive cards, if any. The server reports
 * these as `[from item, to item]` pairs.
 */
export function missingTransferBetween(
    warnings: readonly TimelineWarning[],
    fromId: string,
    toId: string,
): TimelineWarning | undefined {
    return warnings.find(
        (warning) =>
            warning.kind === 'missing_transfer' &&
            warning.item_ids[0] === fromId &&
            warning.item_ids[1] === toId,
    )
}

// ----- lookup -----

/** Find an item anywhere on the timeline (a day card or a lodging bar). */
export function findTimelineItem(timeline: Timeline, id: string): ItineraryItem | undefined {
    for (const span of timeline.lodging) {
        if (span.item.id === id) return span.item
    }
    for (const day of timeline.days) {
        for (const entry of day.entries) {
            if (entry.item.id === id) return entry.item
        }
    }
    return undefined
}
