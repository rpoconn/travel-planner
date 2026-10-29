import { describe, expect, it } from 'vitest'

import { formatDateRange, formatMoney, placeLabel, placeLocality } from './format'

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

describe('formatMoney', () => {
    const currency = (value: number, code: string) =>
        new Intl.NumberFormat(undefined, { style: 'currency', currency: code }).format(value)

    it('formats decimal strings in their currency', () => {
        expect(formatMoney({ amount: '1250.5', currency: 'USD' })).toBe(currency(1250.5, 'USD'))
        expect(formatMoney({ amount: '32000', currency: 'JPY' })).toBe(currency(32000, 'JPY'))
    })

    it('defaults to USD and survives unknown currency codes', () => {
        expect(formatMoney({ amount: '10' })).toBe(currency(10, 'USD'))
        expect(formatMoney({ amount: '10', currency: 'NOT-A-CODE' })).toBe('10 NOT-A-CODE')
    })
})

describe('place labels', () => {
    it('adds the IATA code and builds a locality', () => {
        expect(placeLabel({ name: 'Narita International Airport', iata: 'NRT' })).toBe(
            'Narita International Airport (NRT)',
        )
        expect(placeLabel({ name: 'Hotel Okura' })).toBe('Hotel Okura')
        expect(placeLocality({ name: 'x', city: 'Tokyo', country: 'Japan' })).toBe('Tokyo, Japan')
        expect(placeLocality({ name: 'x' })).toBeNull()
    })
})
