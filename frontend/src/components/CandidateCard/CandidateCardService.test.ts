import { describe, expect, it } from 'vitest'

import { candidate } from '@/test/fixtures'

import { pickLabel, priceLabel } from './CandidateCardService'

describe('priceLabel', () => {
    it('formats the price', () => {
        const label = priceLabel(candidate({ price: { amount: '1800', currency: 'USD' } }))

        expect(label).toContain('1,800')
    })

    it('is null without a price', () => {
        expect(priceLabel(candidate({ price: null }))).toBeNull()
    })
})

describe('pickLabel', () => {
    it('picks at once from a single-pick list', () => {
        expect(pickLabel(false, false)).toBe('Pick')
    })

    it('toggles in a multi-pick list', () => {
        expect(pickLabel(true, false)).toBe('Add')
        expect(pickLabel(true, true)).toBe('Added')
    })
})
