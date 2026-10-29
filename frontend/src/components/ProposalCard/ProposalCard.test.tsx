import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ChatProposal } from '@/api/types.gen'
import { chatProposal } from '@/test/fixtures'

import { ProposalCard, type ProposalAction } from './ProposalCard'

// ----- setup -----

function renderCard(proposal: ChatProposal = chatProposal(), disabled = false) {
    const user = userEvent.setup()
    const onDecide = vi.fn<(action: ProposalAction, message?: string) => void>()
    render(<ProposalCard proposal={proposal} disabled={disabled} onDecide={onDecide} />)
    return { user, onDecide }
}

const card = () => screen.getByRole('region', { name: 'Proposed changes' })

// ----- tests -----

describe('ProposalCard', () => {
    it('lists each change with its key details', () => {
        renderCard(
            chatProposal({
                changes: [
                    ...chatProposal().changes,
                    {
                        call_id: 'c2',
                        name: 'set_plan_details',
                        arguments: {
                            start_date: '2027-05-01',
                            end_date: '2027-05-08',
                            travelers: { adults: 2, children: 1 },
                            origin: { name: 'San Francisco', iata: 'SFO' },
                        },
                        summary: 'Set trip origin, start date, end date, travelers',
                    },
                ],
            }),
        )

        const rows = within(card()).getAllByRole('listitem')
        expect(rows).toHaveLength(2)
        expect(rows[0]).toHaveTextContent('Add lodging “Hotel Okura” on 2027-05-02 15:00')
        expect(rows[0]).toHaveTextContent('Hotel Okura · until 2027-05-06 11:00')
        expect(rows[1]).toHaveTextContent('from SFO')
        expect(rows[1]).toHaveTextContent('2 adults, 1 child')
    })

    it('approves or denies the whole proposal', async () => {
        const { user, onDecide } = renderCard()

        await user.click(screen.getByRole('button', { name: 'Approve' }))
        await user.click(screen.getByRole('button', { name: 'Deny' }))

        expect(onDecide.mock.calls).toEqual([['approve'], ['deny']])
    })

    it('sends "Other…" text as a revision, on Enter or the send button', async () => {
        const { user, onDecide } = renderCard()

        await user.click(screen.getByRole('button', { name: 'Other…' }))
        const box = screen.getByRole('textbox', { name: 'What would you like instead?' })
        expect(box).toHaveFocus()
        expect(screen.getByRole('button', { name: 'Send instead' })).toBeDisabled()

        await user.type(box, '  A ryokan instead{Enter}')
        await user.click(screen.getByRole('button', { name: 'Send instead' }))

        expect(onDecide.mock.calls).toEqual([
            ['revise', 'A ryokan instead'],
            ['revise', 'A ryokan instead'],
        ])
    })

    it('ignores a blank revision', async () => {
        const { user, onDecide } = renderCard()

        await user.click(screen.getByRole('button', { name: 'Other…' }))
        await user.type(screen.getByRole('textbox'), '   {Enter}')

        expect(onDecide).not.toHaveBeenCalled()
    })

    it('is inert while a reply streams', () => {
        renderCard(chatProposal(), true)

        for (const name of ['Approve', 'Deny', 'Other…']) {
            expect(screen.getByRole('button', { name })).toBeDisabled()
        }
    })

    it.each([
        ['approved', 'Approved'],
        ['denied', 'Denied'],
        ['revised', 'Revised'],
        ['superseded', 'Superseded'],
        ['failed', "Couldn't apply"],
    ] as const)('shows a %s proposal with its badge and no buttons', (status, label) => {
        renderCard(chatProposal({ status }))

        expect(within(card()).getByText(label)).toBeInTheDocument()
        expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })
})
