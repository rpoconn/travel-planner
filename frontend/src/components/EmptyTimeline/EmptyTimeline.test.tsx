import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { EmptyTimeline } from './EmptyTimeline'

describe('EmptyTimeline', () => {
    it('explains that the itinerary is empty', () => {
        render(<EmptyTimeline />)

        expect(screen.getByText('No itinerary yet')).toBeInTheDocument()
        expect(screen.getByText(/your days appear here/)).toBeInTheDocument()
    })
})
