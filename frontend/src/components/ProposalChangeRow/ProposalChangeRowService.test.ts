import { describe, expect, it } from 'vitest'

import type { SuggestedChange } from '@/api/types.gen'
import { candidate, SUGGESTION } from '@/test/fixtures'

import { candidateFacts, candidateWarnings } from './ProposalChangeRowService'

const [REMOVAL, ADDITION] = SUGGESTION.changes as [SuggestedChange, SuggestedChange]

describe('candidateFacts', () => {
    it('puts what would be added on one line: when, the details, the price', () => {
        expect(candidateFacts(ADDITION)).toBe('Mon May 3 10:00 to 12:00 · 2h · $40.00')
    })

    it('leaves out what the search did not give', () => {
        const bare = candidate({ subtitle: '', details: [], price: null })

        expect(candidateFacts({ ...ADDITION, candidate: bare })).toBeNull()
    })

    it('has nothing to add to any other change', () => {
        expect(candidateFacts(REMOVAL)).toBeNull()
    })
})

describe('candidateWarnings', () => {
    it('lists how the addition misses what the user asked for', () => {
        const flawed = candidate({ warnings: ['1 stop, not nonstop'] })

        expect(candidateWarnings({ ...ADDITION, candidate: flawed })).toEqual([
            '1 stop, not nonstop',
        ])
        expect(candidateWarnings(REMOVAL)).toEqual([])
    })
})
