import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Candidate } from '@/api/types.gen'
import { candidate } from '@/test/fixtures'
import { render, screen } from '@/test/render'

import { CandidateCard } from './CandidateCard'

// ----- setup -----

function renderCard(option: Candidate, props: { multi?: boolean; selected?: boolean } = {}) {
    const user = userEvent.setup()
    const onPick = vi.fn<() => void>()
    render(<CandidateCard candidate={option} onPick={onPick} {...props} />)
    return { user, onPick }
}

// ----- tests -----

describe('CandidateCard', () => {
    it('shows what the search found', () => {
        renderCard(
            candidate({
                title: 'Example Air EX 101',
                subtitle: 'PHL Sat May 1 09:00 → LIS Sat May 1 22:00',
                details: ['13h', 'nonstop'],
                price: { amount: '1800', currency: 'USD' },
            }),
        )

        const card = screen.getByRole('article', { name: 'Example Air EX 101' })
        expect(card).toHaveTextContent('PHL Sat May 1 09:00 → LIS Sat May 1 22:00')
        expect(card).toHaveTextContent('nonstop')
        expect(card).toHaveTextContent('1,800')
    })

    it('lists how it misses what was asked for', () => {
        renderCard(candidate({ warnings: ['leaves at 18:30, not in the morning'] }))

        expect(screen.getByRole('list', { name: 'Not what you asked for' })).toHaveTextContent(
            'leaves at 18:30, not in the morning',
        )
    })

    it('picks at once from a single-pick list', async () => {
        const { user, onPick } = renderCard(candidate({ title: 'Example Air EX 101' }))

        await user.click(screen.getByRole('button', { name: 'Pick Example Air EX 101' }))

        expect(onPick).toHaveBeenCalledOnce()
    })

    it('toggles in a multi-pick list', () => {
        renderCard(candidate({ title: 'Museum' }), { multi: true, selected: true })

        const button = screen.getByRole('button', { name: 'Added Museum' })
        expect(button).toHaveAttribute('aria-pressed', 'true')
    })

    it('links to the source when there is one', () => {
        renderCard(candidate({ source_url: 'https://example.com/hotel' }))

        expect(screen.getByRole('link', { name: 'Details' })).toHaveAttribute(
            'href',
            'https://example.com/hotel',
        )
    })
})
