import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { timeline, tokyoTimeline } from '@/test/fixtures'

import { TimelineGrid } from './TimelineGrid'

describe('TimelineGrid', () => {
    it('places a column per day and the stay across its nights', () => {
        render(<TimelineGrid timeline={tokyoTimeline()} onSelectItem={vi.fn()} />)

        expect(screen.getAllByRole('region', { name: /^Day \d/ })).toHaveLength(5)
        expect(screen.getByRole('button', { name: 'Hotel Okura, 3 nights' })).toHaveStyle({
            gridColumn: '4 / 10',
        })
        expect(screen.queryByText(/Nothing planned yet/)).not.toBeInTheDocument()
    })

    it('hints that nothing is planned when the days are empty', () => {
        const days = tokyoTimeline().days.map((day) => ({ ...day, entries: [] }))
        render(<TimelineGrid timeline={timeline({ days })} onSelectItem={vi.fn()} />)

        expect(screen.getByText(/Nothing planned yet/)).toBeInTheDocument()
    })

    it('reports the selected item', async () => {
        const onSelectItem = vi.fn()
        render(<TimelineGrid timeline={tokyoTimeline()} onSelectItem={onSelectItem} />)

        await userEvent.click(screen.getByRole('button', { name: 'Hotel Okura, 3 nights' }))

        expect(onSelectItem).toHaveBeenCalledTimes(1)
    })
})
