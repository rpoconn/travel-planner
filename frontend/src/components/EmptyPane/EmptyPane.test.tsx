import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { EmptyPane } from './EmptyPane'

describe('EmptyPane', () => {
    it('renders the icon, title and message', () => {
        render(
            <EmptyPane icon={<svg data-testid="icon" />} title="Itinerary">
                Select a trip.
            </EmptyPane>,
        )

        expect(screen.getByTestId('icon')).toBeInTheDocument()
        expect(screen.getByText('Itinerary')).toBeInTheDocument()
        expect(screen.getByText('Select a trip.')).toBeInTheDocument()
    })
})
