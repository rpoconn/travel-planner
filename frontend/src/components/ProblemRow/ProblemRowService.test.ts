import { describe, expect, it } from 'vitest'

import { OVERLAP, problem } from '@/test/fixtures'

import { canFix, fixLabel, problemText } from './ProblemRowService'

describe('problemText', () => {
    it('capitalises what the trip is missing, so it stands alone', () => {
        expect(problemText(problem({ message: 'a flight home' }))).toBe('A flight home')
    })

    it('leaves a warning as the sentence it is', () => {
        expect(problemText(OVERLAP)).toBe('Senso-ji overlaps Tsukiji Market')
    })
})

describe('fixing', () => {
    it('offers a fix only for what the trip is missing', () => {
        expect(canFix(problem())).toBe(true)
        expect(canFix(OVERLAP)).toBe(false)
    })

    it('names the problem in the button', () => {
        expect(fixLabel(problem({ message: 'a flight home' }))).toBe('Fix this: a flight home')
    })
})
