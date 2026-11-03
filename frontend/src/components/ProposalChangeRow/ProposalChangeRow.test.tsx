import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ProposedChange } from '@/api/types.gen'

import { ProposalChangeRow } from './ProposalChangeRow'

// ----- setup -----

function renderRow(change: ProposedChange) {
    // The row is an <li>, so give it a list to live in.
    render(
        <ul>
            <ProposalChangeRow change={change} />
        </ul>,
    )
}

// ----- tests -----

describe('ProposalChangeRow', () => {
    it('shows the summary with the place and end time as details', () => {
        renderRow({
            call_id: 'c1',
            name: 'add_lodging',
            arguments: {
                place: { name: 'Hotel Okura', city: 'Tokyo' },
                end: '2027-05-06T11:00:00+09:00',
            },
            summary: 'Add lodging “Hotel Okura”',
        })

        expect(screen.getByText('Add lodging “Hotel Okura”')).toBeInTheDocument()
        expect(screen.getByText('Hotel Okura · until 2027-05-06 11:00')).toBeInTheDocument()
    })

    it('omits the details line when there is nothing to show', () => {
        renderRow({
            call_id: 'c2',
            name: 'remove_item',
            arguments: {},
            summary: 'Remove the hotel',
        })

        expect(screen.getByRole('listitem').querySelectorAll('p')).toHaveLength(1)
    })

    it('lists the fields an update changes', () => {
        renderRow({
            call_id: 'c3',
            name: 'update_item',
            arguments: { changes: { title: 'Ryokan' } },
            summary: 'Update the stay',
        })

        expect(screen.getByText('Title: Ryokan')).toBeInTheDocument()
    })
})
