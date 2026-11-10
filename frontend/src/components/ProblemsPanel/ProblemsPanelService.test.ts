import { describe, expect, it } from 'vitest'

import { OVERLAP, problem, travelPlan } from '@/test/fixtures'

import { leftCount, sortProblems, summaryLabel } from './ProblemsPanelService'

const NO_FLIGHT = problem({
    id: 'missing_return',
    code: 'missing_return',
    message: 'a flight home',
})

describe('sortProblems', () => {
    it('lists what the trip is missing before the warnings, keeping their order', () => {
        const stay = problem()

        expect(sortProblems([OVERLAP, NO_FLIGHT, stay])).toEqual([NO_FLIGHT, stay, OVERLAP])
    })
})

describe('leftCount', () => {
    it('counts what the trip is missing, not the warnings', () => {
        expect(leftCount(travelPlan({ problems: [OVERLAP, NO_FLIGHT, problem()] }))).toBe(2)
        expect(leftCount(travelPlan())).toBe(0)
    })
})

describe('summaryLabel', () => {
    it('counts in words', () => {
        expect(summaryLabel(1)).toBe('1 thing left')
        expect(summaryLabel(3)).toBe('3 things left')
    })
})
