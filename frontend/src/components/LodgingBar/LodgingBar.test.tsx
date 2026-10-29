import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { lodgingItem } from '@/test/fixtures'

import { LodgingBar } from './LodgingBar'

describe('LodgingBar', () => {
    it('shows the stay with its number of nights and grid placement', () => {
        render(
            <LodgingBar
                item={lodgingItem()}
                style={{ gridColumn: '4 / 10', gridRow: 2 }}
                onSelect={vi.fn()}
            />,
        )

        const bar = screen.getByRole('button', { name: 'Hotel Okura, 3 nights' })
        expect(bar).toHaveStyle({ gridColumn: '4 / 10' })
        expect(bar).toHaveAttribute('title', expect.stringMatching(/^Check-in .* · Check-out /))
    })

    it('uses the singular for one night and reports clicks', async () => {
        const onSelect = vi.fn()
        const item = lodgingItem({ end: '2027-05-03T11:00:00+09:00' })
        render(<LodgingBar item={item} onSelect={onSelect} />)

        await userEvent.click(screen.getByRole('button', { name: 'Hotel Okura, 1 night' }))

        expect(onSelect).toHaveBeenCalledWith(item)
    })
})
