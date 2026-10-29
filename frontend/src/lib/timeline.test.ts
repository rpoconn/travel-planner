import { describe, expect, it } from 'vitest'

import type { LodgingSpan, TimelineWarning } from '@/api/types.gen'
import {
    activityItem,
    flightItem,
    lodgingItem,
    timelineEntry,
    tokyoTimeline,
} from '@/test/fixtures'

import {
    dayGridColumn,
    dayOffset,
    dayOffsetSuffix,
    entryTimeLabel,
    findTimelineItem,
    formatDayHeader,
    formatDuration,
    formatWallTime,
    layoutLodging,
    missingTransferBetween,
    parseWallClock,
    warningsByDate,
    warningsByItem,
} from './timeline'

// ----- setup -----

/** Expected locale output for a wall-clock time (tests run under whatever locale Node has). */
const time = (hour: number, minute: number) =>
    new Intl.DateTimeFormat(undefined, {
        hour: 'numeric',
        minute: '2-digit',
        timeZone: 'UTC',
    }).format(new Date(Date.UTC(2027, 0, 1, hour, minute)))

function span(id: string, start: number, end: number): LodgingSpan {
    return { item: lodgingItem({ id, title: id }), start_day_index: start, end_day_index: end }
}

function warning(overrides: Partial<TimelineWarning>): TimelineWarning {
    return { kind: 'overlap', message: 'msg', date: '2027-05-02', item_ids: [], ...overrides }
}

// ----- wall-clock times -----

describe('wall-clock times', () => {
    it('reads the local time of the place, ignoring the viewer timezone', () => {
        expect(parseWallClock('2027-05-02T14:30:00+09:00')).toEqual({
            year: 2027,
            month: 5,
            day: 2,
            hour: 14,
            minute: 30,
        })
        expect(formatWallTime('2027-05-02T14:30:00+09:00')).toBe(time(14, 30))
        expect(formatWallTime('2027-05-01T23:05:00-07:00')).toBe(time(23, 5))
    })

    it('treats a plain date as midnight and rejects garbage', () => {
        expect(parseWallClock('2027-05-02')).toMatchObject({ day: 2, hour: 0, minute: 0 })
        expect(() => parseWallClock('soon')).toThrow('Not an ISO date/time')
    })

    it('formats day headers from calendar dates', () => {
        const header = formatDayHeader('2027-05-01')
        // 2027-05-01 is a Saturday in every timezone because no time conversion happens.
        expect(header.weekday).toBe(
            new Intl.DateTimeFormat(undefined, { weekday: 'short', timeZone: 'UTC' }).format(
                new Date(Date.UTC(2027, 4, 1)),
            ),
        )
        expect(header.date).toMatch(/1/)
    })
})

describe('dayOffset / duration', () => {
    it('counts local calendar days between departure and arrival', () => {
        // SFO 11:00 May 1 → NRT 14:30 May 2 (local).
        expect(dayOffset('2027-05-01T11:00:00-07:00', '2027-05-02T14:30:00+09:00')).toBe(1)
        expect(dayOffsetSuffix('2027-05-01T11:00:00-07:00', '2027-05-02T14:30:00+09:00')).toBe(
            ' (+1)',
        )
    })

    it('goes negative for an eastbound date-line crossing', () => {
        // Tokyo 01:00 May 5 → Honolulu 13:00 May 4 (local): lands "yesterday".
        expect(dayOffset('2027-05-05T01:00:00+09:00', '2027-05-04T13:00:00-10:00')).toBe(-1)
        expect(dayOffsetSuffix('2027-05-05T01:00:00+09:00', '2027-05-04T13:00:00-10:00')).toBe(
            ' (−1)',
        )
        expect(dayOffsetSuffix('2027-05-05T09:00:00+09:00', '2027-05-05T11:00:00+09:00')).toBe('')
    })

    it('measures real elapsed time across offsets', () => {
        // 11:00 PDT (18:00Z) → 14:30 JST next day (05:30Z) = 11h 30m.
        expect(formatDuration('2027-05-01T11:00:00-07:00', '2027-05-02T14:30:00+09:00')).toBe(
            '11h 30m',
        )
        expect(formatDuration('2027-05-02T15:30:00+09:00', '2027-05-02T16:15:00+09:00')).toBe('45m')
        expect(formatDuration('2027-05-02T15:00:00+09:00', '2027-05-02T17:00:00+09:00')).toBe('2h')
    })
})

describe('entryTimeLabel', () => {
    const flight = flightItem()

    it('shows the full range for a same-day item', () => {
        expect(entryTimeLabel(timelineEntry(activityItem()))).toBe(`${time(9, 0)} – ${time(11, 0)}`)
    })

    it('shows only the relevant end of an item crossing midnight', () => {
        const overnight = activityItem({
            start: '2027-05-03T20:00:00+09:00',
            end: '2027-05-05T02:00:00+09:00',
        })
        expect(entryTimeLabel(timelineEntry(overnight, { continues_to_next_day: true }))).toBe(
            `${time(20, 0)} →`,
        )
        expect(
            entryTimeLabel(timelineEntry(overnight, { continues_from_previous_day: true })),
        ).toBe(`Until ${time(2, 0)}`)
        expect(
            entryTimeLabel(
                timelineEntry(overnight, {
                    continues_from_previous_day: true,
                    continues_to_next_day: true,
                }),
            ),
        ).toBe('All day')
    })

    it('labels each day of a flight that lands on a later local day', () => {
        // Departure card: both local times, with the arrival's day offset.
        expect(entryTimeLabel(timelineEntry(flight, { continues_to_next_day: true }))).toBe(
            `${time(11, 0)} → ${time(14, 30)} (+1)`,
        )
        expect(entryTimeLabel(timelineEntry(flight, { continues_from_previous_day: true }))).toBe(
            `Arrives ${time(14, 30)}`,
        )
        expect(
            entryTimeLabel(
                timelineEntry(flight, {
                    continues_from_previous_day: true,
                    continues_to_next_day: true,
                }),
            ),
        ).toBe('In flight')
    })

    it('shows a single time for an instant', () => {
        const start = '2027-05-03T09:00:00+09:00'
        expect(entryTimeLabel(timelineEntry(activityItem({ start, end: start })))).toBe(time(9, 0))
    })
})

// ----- lodging layout -----

describe('layoutLodging', () => {
    it('draws a stay from mid check-in day to mid check-out day', () => {
        const { placements, laneCount } = layoutLodging([span('okura', 1, 4)])

        expect(laneCount).toBe(1)
        // Day 1 covers half-column lines 3..5 (middle = 4); day 4's middle is line 10.
        expect(placements[0]).toMatchObject({ lane: 0, gridColumnStart: 4, gridColumnEnd: 10 })
    })

    it('lets back-to-back stays share a lane', () => {
        const { placements, laneCount } = layoutLodging([span('kyoto', 3, 5), span('tokyo', 0, 3)])

        expect(laneCount).toBe(1)
        expect(placements.map((p) => [p.span.item.id, p.lane])).toEqual([
            ['tokyo', 0],
            ['kyoto', 0],
        ])
    })

    it('stacks overlapping stays into separate lanes and reuses freed lanes', () => {
        const { placements, laneCount } = layoutLodging([
            span('a', 0, 3),
            span('b', 1, 2),
            span('c', 2, 5),
            span('d', 3, 4),
        ])

        expect(laneCount).toBe(2)
        expect(Object.fromEntries(placements.map((p) => [p.span.item.id, p.lane]))).toEqual({
            a: 0,
            b: 1,
            c: 1, // b frees lane 1 at day 2's middle, exactly when c starts
            d: 0, // a frees lane 0 at day 3's middle
        })
    })

    it('gives a same-day stay at least one half-column', () => {
        const { placements } = layoutLodging([span('dayroom', 2, 2)])

        expect(placements[0]).toMatchObject({ gridColumnStart: 6, gridColumnEnd: 7 })
    })

    it('handles no lodging', () => {
        expect(layoutLodging([])).toEqual({ placements: [], laneCount: 0 })
    })

    it('places day columns on the half-column grid', () => {
        expect(dayGridColumn(0)).toBe('1 / span 2')
        expect(dayGridColumn(3)).toBe('7 / span 2')
    })
})

// ----- warnings & lookup -----

describe('warnings', () => {
    const overlap = warning({ item_ids: ['a', 'b'] })
    const gap = warning({ kind: 'missing_transfer', item_ids: ['b', 'c'], date: '2027-05-03' })
    const undated = warning({ kind: 'outside_trip', date: null, item_ids: ['c'] })

    it('groups by date, skipping undated ones', () => {
        const byDate = warningsByDate([overlap, gap, undated])

        expect(byDate.get('2027-05-02')).toEqual([overlap])
        expect(byDate.get('2027-05-03')).toEqual([gap])
        expect(byDate.size).toBe(2)
    })

    it('indexes by every item mentioned', () => {
        const byItem = warningsByItem([overlap, gap, undated])

        expect(byItem.get('b')).toEqual([overlap, gap])
        expect(byItem.get('c')).toEqual([gap, undated])
    })

    it('finds a missing transfer only for that ordered pair', () => {
        expect(missingTransferBetween([overlap, gap], 'b', 'c')).toBe(gap)
        expect(missingTransferBetween([overlap, gap], 'c', 'b')).toBeUndefined()
        expect(missingTransferBetween([overlap, gap], 'a', 'b')).toBeUndefined()
    })
})

describe('findTimelineItem', () => {
    it('finds cards and lodging, and returns undefined for removed items', () => {
        const data = tokyoTimeline()

        expect(findTimelineItem(data, 'activity-1')?.title).toBe('Senso-ji Temple')
        expect(findTimelineItem(data, 'lodging-1')?.type).toBe('lodging')
        expect(findTimelineItem(data, 'gone')).toBeUndefined()
    })
})
