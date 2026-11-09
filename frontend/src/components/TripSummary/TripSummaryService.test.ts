import { describe, expect, it } from 'vitest'

import { formatDateRange } from '@/lib/format'
import { activityItem, timeline, timelineEntry, tokyoTimeline, travelPlan } from '@/test/fixtures'

import { timelineCounts, timelineItemCount, tripSummaryLine } from './TripSummaryService'

// ----- setup -----

// Dates are formatted in the runner's locale, so build the expected text the same way.
const TOKYO_DATES = formatDateRange('2027-05-01', '2027-05-08') ?? ''

// ----- tests -----

describe('timelineItemCount', () => {
    it('counts a flight shown on two days once, and the lodging bars', () => {
        // Flight (days 1 and 2), transfer, activity, plus the hotel.
        expect(timelineItemCount(tokyoTimeline())).toBe(4)
    })

    it('is zero for an empty timeline', () => {
        expect(timelineItemCount(timeline())).toBe(0)
    })
})

describe('timelineCounts', () => {
    it('gives the day and item counts', () => {
        expect(timelineCounts(tokyoTimeline())).toBe('5 days · 4 items')
    })

    it('uses the singular for one day and one item', () => {
        const oneDay = timeline({
            days: [
                {
                    index: 0,
                    date: '2027-05-01',
                    entries: [timelineEntry(activityItem())],
                    timezone: 'Asia/Tokyo',
                },
            ],
        })

        expect(timelineCounts(oneDay)).toBe('1 day · 1 item')
    })

    it('is null when there are no days', () => {
        expect(timelineCounts(timeline())).toBeNull()
    })
})

describe('tripSummaryLine', () => {
    it('joins where, when and the counts', () => {
        expect(tripSummaryLine(travelPlan(), tokyoTimeline())).toBe(
            `Tokyo · ${TOKYO_DATES} · 5 days · 4 items`,
        )
    })

    it('leaves the counts out while the timeline loads', () => {
        expect(tripSummaryLine(travelPlan(), undefined)).toBe(`Tokyo · ${TOKYO_DATES}`)
    })

    it('is empty for a trip with nothing known yet', () => {
        const blank = travelPlan({ destination: null, start_date: null, end_date: null })

        expect(tripSummaryLine(blank, timeline())).toBe('')
    })
})
