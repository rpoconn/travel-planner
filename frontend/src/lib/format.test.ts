import { describe, expect, it } from 'vitest'

import { formatDateRange } from './format'

const monthDay = (year: number, month: number, day: number) =>
    new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(
        new Date(year, month - 1, day),
    )

describe('formatDateRange', () => {
    it('formats a full range using local calendar dates', () => {
        // Parsed as local dates, so the day never shifts regardless of the test machine's TZ.
        expect(formatDateRange('2027-05-01', '2027-05-08')).toBe(
            `${monthDay(2027, 5, 1)} – ${monthDay(2027, 5, 8)}`,
        )
    })

    it('handles open-ended ranges', () => {
        expect(formatDateRange('2027-05-01', null)).toBe(`From ${monthDay(2027, 5, 1)}`)
        expect(formatDateRange(null, '2027-05-08')).toBe(`Until ${monthDay(2027, 5, 8)}`)
    })

    it('returns null without dates', () => {
        expect(formatDateRange(null, undefined)).toBeNull()
    })
})
