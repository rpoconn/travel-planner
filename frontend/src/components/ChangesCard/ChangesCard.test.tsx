import { describe, expect, it } from 'vitest'

import { render, screen, within } from '@/test/render'

import { ChangesCard } from './ChangesCard'

describe('ChangesCard', () => {
    it('lists each change written', () => {
        render(
            <ChangesCard
                record={{
                    type: 'changes',
                    applied: [
                        { name: 'remove_item', summary: 'Removed “Riverside Hotel”', item_id: 'a' },
                        {
                            name: 'set_travel_mode',
                            summary: 'The trip has no flights',
                            item_id: null,
                        },
                    ],
                    rejected: [],
                    left_open: [],
                    clarification: null,
                }}
            />,
        )

        const card = screen.getByRole('region', { name: 'Trip updated' })
        expect(
            within(card)
                .getAllByRole('listitem')
                .map((item) => item.textContent),
        ).toEqual(['Removed “Riverside Hotel”', 'The trip has no flights'])
    })

    it('names a ride the change left missing, after what it changed', () => {
        render(
            <ChangesCard
                record={{
                    type: 'changes',
                    applied: [
                        {
                            name: 'remove_item',
                            summary: 'Removed “Museum” and “Taxi to lunch”',
                            item_id: 'a',
                        },
                    ],
                    rejected: [],
                    left_open: [
                        "Nothing gets you from Friend's flat to Time Out Market now. I'll look for a ride",
                    ],
                    clarification: null,
                }}
            />,
        )

        const card = screen.getByRole('region', { name: 'Trip updated' })
        expect(
            within(card)
                .getAllByRole('listitem')
                .map((item) => item.textContent),
        ).toEqual([
            'Removed “Museum” and “Taxi to lunch”',
            "Nothing gets you from Friend's flat to Time Out Market now. I'll look for a ride.",
        ])
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
                    left_open: [],
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
