import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { TimelineWarning } from '@/api/types.gen'
import { flightItem, timelineEntry } from '@/test/fixtures'
import { render, screen } from '@/test/render'

import { ContinuationCard } from './ContinuationCard'

describe('ContinuationCard', () => {
    it('marks an in-flight day and reports clicks', async () => {
        const onSelect = vi.fn()
        const flight = flightItem()
        render(
            <ContinuationCard
                entry={timelineEntry(flight, { continues_from_previous_day: true })}
                arriving={false}
                warnings={[]}
                onSelect={onSelect}
            />,
        )

        const card = screen.getByRole('button')
        expect(card).toHaveAttribute('data-continuation', 'in_flight')
        expect(card).toHaveTextContent(/departed/)

        await userEvent.click(card)
        expect(onSelect).toHaveBeenCalledWith(flight)
    })

    it('marks the arrival day and lists warnings for screen readers', () => {
        const warning: TimelineWarning = {
            kind: 'wrong_timezone',
            message: 'Check the landing time',
            date: null,
            item_ids: [],
        }
        render(
            <ContinuationCard
                entry={timelineEntry(flightItem(), { continues_from_previous_day: true })}
                arriving
                warnings={[warning]}
                onSelect={vi.fn()}
            />,
        )

        expect(screen.getByRole('button')).toHaveAttribute('data-continuation', 'arrival')
        expect(screen.getByText(/Lands at/)).toBeInTheDocument()
        expect(screen.getByText('Check the landing time')).toHaveClass('sr-only')
    })
})
