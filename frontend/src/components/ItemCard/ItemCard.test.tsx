import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { formatMoney } from '@/lib/format'
import { activityItem, flightItem, timelineEntry } from '@/test/fixtures'

import { ItemCard } from './ItemCard'

describe('ItemCard', () => {
    it('summarizes a flight with route, number, duration and direction', () => {
        render(
            <ItemCard
                entry={timelineEntry(flightItem(), { continues_to_next_day: true })}
                onSelect={vi.fn()}
            />,
        )

        const card = screen.getByRole('button', { name: /SFO → NRT/ })
        expect(card).toHaveAttribute('data-item-type', 'flight')
        expect(card).toHaveTextContent('Outbound')
        expect(card).toHaveTextContent('SFO → NRT · United UA 837 · 11h 30m')
    })

    it('shows category, place, status and cost for an activity', () => {
        render(
            <ItemCard
                entry={timelineEntry(
                    activityItem({ status: 'confirmed', cost: { amount: '20', currency: 'USD' } }),
                )}
                onSelect={vi.fn()}
            />,
        )

        const card = screen.getByRole('button', { name: /Senso-ji Temple/ })
        expect(card).toHaveTextContent('Sightseeing · Senso-ji')
        expect(card).toHaveTextContent('Confirmed')
        // No basis means an estimate.
        expect(card).toHaveTextContent(`~${formatMoney({ amount: '20', currency: 'USD' })}`)
    })

    it('shows a quoted price without the estimate mark', () => {
        render(
            <ItemCard
                entry={timelineEntry(
                    activityItem({ cost: { amount: '20', currency: 'USD', basis: 'exact' } }),
                )}
                onSelect={vi.fn()}
            />,
        )

        const card = screen.getByRole('button', { name: /Senso-ji Temple/ })
        expect(card).toHaveTextContent(formatMoney({ amount: '20', currency: 'USD' }))
        expect(card).not.toHaveTextContent('~')
    })

    it('flags items that have warnings', () => {
        render(
            <ItemCard
                entry={timelineEntry(activityItem())}
                warnings={[
                    {
                        kind: 'overlap',
                        message: 'Senso-ji Temple overlaps Lunch',
                        date: '2027-05-03',
                        item_ids: ['activity-1'],
                    },
                ]}
                onSelect={vi.fn()}
            />,
        )

        expect(screen.getByRole('button', { name: /Senso-ji Temple overlaps Lunch/ })).toBeVisible()
    })

    it('reports clicks with the item', async () => {
        const onSelect = vi.fn()
        render(<ItemCard entry={timelineEntry(activityItem())} onSelect={onSelect} />)

        await userEvent.click(screen.getByRole('button', { name: /Senso-ji Temple/ }))

        expect(onSelect).toHaveBeenCalledWith(activityItem())
    })
})
