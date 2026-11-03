import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { timeline, tokyoTimeline } from '@/test/fixtures'

import { TimelineHeader } from './TimelineHeader'

describe('TimelineHeader', () => {
    it('shows day and item counts', () => {
        render(<TimelineHeader timeline={tokyoTimeline()} />)

        expect(screen.getByRole('heading', { name: 'Itinerary' })).toBeInTheDocument()
        expect(screen.getByText('5 days · 4 items')).toBeInTheDocument()
    })

    it('omits the counts when there are no days', () => {
        render(<TimelineHeader timeline={timeline()} />)

        expect(screen.queryByText(/day/)).not.toBeInTheDocument()
    })
})
