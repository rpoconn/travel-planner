import { describe, expect, it } from 'vitest'

import { formatDateRange } from '@/lib/format'
import { OFFER_ACTIVITIES, OFFER_FLIGHTS_OUT, OFFER_LODGING } from '@/test/fixtures'

import {
    canSubmit,
    isMulti,
    nightsHint,
    pickHint,
    submitLabel,
    toggleId,
} from './ChoiceListService'

describe('isMulti', () => {
    it('is true when the offer takes several picks', () => {
        expect(isMulti(OFFER_ACTIVITIES)).toBe(true)
        expect(isMulti(OFFER_FLIGHTS_OUT)).toBe(false)
    })
})

describe('toggleId', () => {
    it('adds and removes an id', () => {
        expect(toggleId(['1'], '2', 3)).toEqual(['1', '2'])
        expect(toggleId(['1', '2'], '1', 3)).toEqual(['2'])
    })

    it('ignores an add past the most the offer takes', () => {
        expect(toggleId(['1', '2'], '3', 2)).toEqual(['1', '2'])
    })
})

describe('canSubmit', () => {
    it('needs a count within the offer bounds', () => {
        expect(canSubmit([], OFFER_ACTIVITIES)).toBe(false)
        expect(canSubmit(['1'], OFFER_ACTIVITIES)).toBe(true)
        expect(canSubmit(['1', '2', '3'], OFFER_ACTIVITIES)).toBe(true)
    })
})

describe('submitLabel', () => {
    it('says what is still needed, then how many it adds', () => {
        expect(submitLabel([], OFFER_ACTIVITIES)).toBe('Pick at least 1')
        expect(submitLabel(['1'], OFFER_ACTIVITIES)).toBe('Add 1 pick')
        expect(submitLabel(['1', '3'], OFFER_ACTIVITIES)).toBe('Add 2 picks')
    })
})

describe('pickHint', () => {
    it('gives the bounds of a multi pick only', () => {
        expect(pickHint(OFFER_ACTIVITIES)).toBe('Pick 1 to 3.')
        expect(pickHint(OFFER_FLIGHTS_OUT)).toBeNull()
    })
})

describe('nightsHint', () => {
    it('says which nights a lodging offer is for', () => {
        const dates = formatDateRange('2027-05-01', '2027-05-03') ?? ''
        expect(nightsHint(OFFER_LODGING)).toBe(`2 nights, ${dates}.`)
        const oneNight = {
            ...OFFER_LODGING,
            nights: { check_in: '2027-05-01', check_out: '2027-05-02' },
        }
        expect(nightsHint(oneNight)).toMatch(/^1 night, /)
    })

    it('is nothing for an offer without nights', () => {
        expect(nightsHint(OFFER_FLIGHTS_OUT)).toBeNull()
    })
})
