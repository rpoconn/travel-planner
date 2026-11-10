import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SuggestedChange } from '@/api/types.gen'
import { candidate, SUGGESTION } from '@/test/fixtures'

import { ProposalChangeRow } from './ProposalChangeRow'

const [REMOVAL, ADDITION] = SUGGESTION.changes as [SuggestedChange, SuggestedChange]

describe('ProposalChangeRow', () => {
    it('says what the change would do', () => {
        render(<ProposalChangeRow change={REMOVAL} />)

        expect(screen.getByRole('listitem')).toHaveTextContent(/^Remove “Dinner at Ramiro”$/)
    })

    it('shows the facts of something a search found', () => {
        render(<ProposalChangeRow change={ADDITION} />)

        const row = screen.getByRole('listitem')
        expect(row).toHaveTextContent('Add “Tram tour”')
        expect(row).toHaveTextContent('Mon May 3 10:00 to 12:00 · 2h · $40.00')
    })

    it('flags how the addition misses what was asked for', () => {
        const flawed = candidate({ warnings: ['1 stop, not nonstop'] })
        render(<ProposalChangeRow change={{ ...ADDITION, candidate: flawed }} />)

        expect(screen.getByRole('listitem')).toHaveTextContent('1 stop, not nonstop')
    })
})
