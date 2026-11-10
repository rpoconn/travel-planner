import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SUGGESTION } from '@/test/fixtures'

import { ProposalCard } from './ProposalCard'

const card = () => screen.getByRole('region', { name: 'Suggested changes' })

describe('ProposalCard', () => {
    it('gives the reason and lists each change', () => {
        render(<ProposalCard proposal={SUGGESTION} onDecide={vi.fn()} />)

        expect(card()).toHaveTextContent('A quieter second day.')
        const rows = within(card()).getAllByRole('listitem')
        expect(rows).toHaveLength(2)
        expect(rows[0]).toHaveTextContent('Remove “Dinner at Ramiro”')
        expect(rows[1]).toHaveTextContent('Add “Tram tour”')
    })

    it('sends the decision of the button pressed', async () => {
        const onDecide = vi.fn()
        render(<ProposalCard proposal={SUGGESTION} onDecide={onDecide} />)

        await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
        await userEvent.click(screen.getByRole('button', { name: 'Reject' }))

        expect(onDecide.mock.calls).toEqual([['approve'], ['reject']])
    })

    it('locks both buttons while a turn streams', () => {
        render(<ProposalCard proposal={SUGGESTION} disabled onDecide={vi.fn()} />)

        expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled()
    })
})
