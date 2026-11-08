import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ChangesCard } from './ChangesCard'

describe('ChangesCard', () => {
    it('lists each change written', () => {
        render(
            <ChangesCard
                record={{
                    type: 'changes',
                    applied: [
                        { name: 'remove_item', summary: 'Removed “Riverside Hotel”', item_id: 'a' },
                        { name: 'change_stage', summary: 'Back to lodging', item_id: null },
                    ],
                    rejected: [],
                    clarification: null,
                }}
            />,
        )

        const card = screen.getByRole('region', { name: 'Trip updated' })
        expect(
            within(card)
                .getAllByRole('listitem')
                .map((item) => item.textContent),
        ).toEqual(['Removed “Riverside Hotel”', 'Back to lodging'])
    })

    it('shows a change the rules turned down with the reason', () => {
        render(
            <ChangesCard
                record={{
                    type: 'changes',
                    applied: [],
                    rejected: [
                        {
                            summary: 'Change “Dinner”: start',
                            reason: 'that is during the flight home',
                        },
                    ],
                    clarification: null,
                }}
            />,
        )

        const card = screen.getByRole('region', { name: 'Nothing changed' })
        expect(within(card).getByRole('listitem')).toHaveTextContent(
            'Change “Dinner”: startthat is during the flight home',
        )
    })
})
