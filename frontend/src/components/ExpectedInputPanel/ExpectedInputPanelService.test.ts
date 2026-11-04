import { describe, expect, it } from 'vitest'

import type { FieldSpec } from '@/api/types.gen'
import { ASK_FLIGHT_PREFERENCES } from '@/test/fixtures'

import {
    buildValue,
    earliestEnd,
    initialFormState,
    partKey,
    setText,
    togglePick,
    type FormState,
} from './ExpectedInputPanelService'

// ----- setup -----

function field(overrides: Partial<FieldSpec>): FieldSpec {
    return {
        name: 'field',
        label: 'Field',
        kind: 'text',
        required: true,
        options: [],
        min: null,
        max: null,
        placeholder: '',
        ...overrides,
    }
}

const DATES = field({ name: 'dates', label: 'Trip dates', kind: 'date_range' })
const ADULTS = field({ name: 'adults', label: 'Adults', kind: 'integer', min: 1, max: 20 })
const CHILDREN = field({ name: 'children', label: 'Children', kind: 'integer', required: false })
const BUDGET = field({ name: 'budget', label: 'Total budget', kind: 'money' })
const PLACE = field({ name: 'destination', label: 'Destination', kind: 'place' })
const NOTE = field({ name: 'neighborhood', label: 'Neighborhood', required: false })
const [CABIN, STOPS] = ASK_FLIGHT_PREFERENCES.fields as [FieldSpec, FieldSpec]
const INTERESTS = field({
    name: 'interests',
    label: 'Interests',
    kind: 'multi_choice',
    min: 1,
    max: 2,
    options: [
        { value: 'museum', label: 'Museum' },
        { value: 'food', label: 'Food' },
        { value: 'outdoors', label: 'Outdoors' },
    ],
})

function state(text: Record<string, string> = {}, picks: Record<string, number[]> = {}): FormState {
    return { text, picks }
}

// ----- tests -----

describe('initialFormState', () => {
    it('fills inputs from the prefill', () => {
        const form = initialFormState([DATES, BUDGET, PLACE, ADULTS], {
            dates: { start: '2027-05-01', end: '2027-05-08' },
            budget: { amount: '4000', currency: 'EUR' },
            destination: { name: 'Lisbon', country: 'Portugal' },
            adults: 2,
        })

        expect(form.text).toEqual({
            'dates.start': '2027-05-01',
            'dates.end': '2027-05-08',
            'budget.amount': '4000',
            'budget.currency': 'EUR',
            destination: 'Lisbon',
            adults: '2',
        })
    })

    it('starts counts at their minimum and money in dollars', () => {
        const form = initialFormState([ADULTS, CHILDREN, BUDGET], {})

        expect(form.text.adults).toBe('1')
        expect(form.text.children).toBe('')
        expect(form.text[partKey('budget', 'currency')]).toBe('USD')
    })

    it('starts a required choice on its first option and an optional one on "Any"', () => {
        const form = initialFormState([CABIN, STOPS], {})

        expect(form.picks).toEqual({ cabin: [0], max_stops: [2] })
    })

    it('picks the prefilled options, including a prefilled null', () => {
        const form = initialFormState([CABIN, STOPS, INTERESTS], {
            cabin: 'business',
            max_stops: null,
            interests: ['food', 'museum'],
        })

        expect(form.picks).toEqual({ cabin: [1], max_stops: [2], interests: [0, 1] })
    })
})

describe('edits', () => {
    it('sets one text input', () => {
        expect(setText(state({ a: '1' }), 'b', '2').text).toEqual({ a: '1', b: '2' })
    })

    it('replaces a choice and toggles a multi choice up to its max', () => {
        expect(togglePick(state({}, { cabin: [0] }), CABIN, 1).picks.cabin).toEqual([1])

        let form = togglePick(state(), INTERESTS, 2)
        form = togglePick(form, INTERESTS, 0)
        expect(form.picks.interests).toEqual([0, 2])
        expect(togglePick(form, INTERESTS, 1)).toBe(form) // already at the max of 2
        expect(togglePick(form, INTERESTS, 2).picks.interests).toEqual([0])
    })

    it("limits a range's end to its start", () => {
        expect(earliestEnd(state({ 'dates.start': '2027-05-01' }), 'dates')).toBe('2027-05-01')
        expect(earliestEnd(state({ 'dates.start': '' }), 'dates')).toBeUndefined()
    })
})

describe('buildValue', () => {
    it('builds the slot value from every kind of field', () => {
        const result = buildValue(
            [DATES, ADULTS, BUDGET, PLACE, CABIN, STOPS, INTERESTS],
            state(
                {
                    'dates.start': '2027-05-01',
                    'dates.end': '2027-05-08',
                    adults: '2',
                    'budget.amount': '4000',
                    'budget.currency': 'eur',
                    destination: '  Lisbon ',
                },
                { cabin: [1], max_stops: [2], interests: [0, 1] },
            ),
        )

        expect(result).toEqual({
            ok: true,
            value: {
                dates: { start: '2027-05-01', end: '2027-05-08' },
                adults: 2,
                budget: { amount: 4000, currency: 'EUR' },
                destination: { name: 'Lisbon' },
                cabin: 'business',
                max_stops: null,
                interests: ['museum', 'food'],
            },
        })
    })

    it('leaves empty optional fields out', () => {
        expect(buildValue([CHILDREN, NOTE], state({ children: '', neighborhood: ' ' }))).toEqual({
            ok: true,
            value: {},
        })
    })

    it.each([
        [DATES, state({ 'dates.start': '2027-05-01' }), 'Pick a start and an end date.'],
        [
            DATES,
            state({ 'dates.start': '2027-05-08', 'dates.end': '2027-05-01' }),
            'The trip has to end on or after the day it starts.',
        ],
        [ADULTS, state({ adults: '0' }), 'Adults has to be at least 1.'],
        [ADULTS, state({ adults: '21' }), 'Adults can be at most 20.'],
        [ADULTS, state({ adults: '1.5' }), 'Adults has to be a whole number.'],
        [BUDGET, state({ 'budget.amount': '-5' }), 'Total budget has to be a positive number.'],
        [
            BUDGET,
            state({ 'budget.amount': '10', 'budget.currency': 'dollars' }),
            'Use a three-letter currency code, like USD or EUR.',
        ],
        [PLACE, state({ destination: '' }), 'Destination is required.'],
        [CABIN, state(), 'Choose a cabin.'],
        [INTERESTS, state({}, { interests: [] }), 'Choose at least 1 interests.'],
    ])('rejects a bad %s value', (spec, form, error) => {
        expect(buildValue([spec], form)).toEqual({ ok: false, error })
    })
})
