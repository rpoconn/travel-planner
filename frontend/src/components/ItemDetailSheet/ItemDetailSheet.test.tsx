import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ItineraryItem } from '@/lib/itemMeta'
import { flightItem, lodgingItem, transferItem } from '@/test/fixtures'

import { ItemDetailSheet } from './ItemDetailSheet'

// ----- setup -----

function renderSheet(item: ItineraryItem | null) {
    const onClose = vi.fn()
    const result = render(<ItemDetailSheet item={item} onClose={onClose} />)
    return { ...result, onClose }
}

/** The <dd> next to a <dt> label. */
function field(label: string): HTMLElement {
    const term = within(screen.getByRole('dialog')).getByText(label, { selector: 'dt' })
    const value = term.nextElementSibling
    if (!(value instanceof HTMLElement)) throw new Error(`No value for ${label}`)
    return value
}

// ----- tests -----

describe('ItemDetailSheet', () => {
    it('is closed without an item', () => {
        renderSheet(null)

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('shows every flight field, status, cost and a safe source link', () => {
        renderSheet(
            flightItem({
                status: 'confirmed',
                cost: { amount: '1250', currency: 'USD' },
                booking_ref: 'ABC123',
                source_url: 'https://www.united.com/fares',
                notes: 'Window seats',
            }),
        )

        const dialog = screen.getByRole('dialog', { name: 'SFO → NRT' })
        expect(within(dialog).getByText('Flight', { selector: 'span' })).toBeInTheDocument()
        expect(within(dialog).getByText('Confirmed')).toBeInTheDocument()
        expect(field('Flight')).toHaveTextContent('United UA 837')
        expect(field('From')).toHaveTextContent('San Francisco International (SFO)')
        expect(field('To')).toHaveTextContent('Narita International Airport (NRT)')
        expect(field('Duration')).toHaveTextContent('11h 30m')
        expect(field('Layovers')).toHaveTextContent('Nonstop')
        expect(field('Time zone')).toHaveTextContent('America/Los_Angeles → Asia/Tokyo')
        expect(field('Booking ref')).toHaveTextContent('ABC123')
        expect(field('Notes')).toHaveTextContent('Window seats')
        expect(field('Added by')).toHaveTextContent('Assistant')

        const link = within(dialog).getByRole('link', { name: /www\.united\.com/ })
        expect(link).toHaveAttribute('href', 'https://www.united.com/fares')
        expect(link).toHaveAttribute('target', '_blank')
        expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    })

    it('shows lodging specifics and hides empty optional fields', () => {
        renderSheet(
            lodgingItem({ room_type: 'Twin', nightly_rate: { amount: '300', currency: 'USD' } }),
        )

        expect(field('Where')).toHaveTextContent('Hotel Okura')
        expect(field('Where')).toHaveTextContent('Tokyo, Japan')
        expect(field('Nights')).toHaveTextContent('3')
        expect(field('Room')).toHaveTextContent('Twin')
        expect(within(screen.getByRole('dialog')).queryByText('Cost')).not.toBeInTheDocument()
        expect(within(screen.getByRole('dialog')).queryByText('Source')).not.toBeInTheDocument()
    })

    it('shows transfer specifics', () => {
        renderSheet(transferItem())

        expect(field('Mode')).toHaveTextContent('Train')
        expect(field('Provider')).toHaveTextContent('JR East')
        expect(field('To')).toHaveTextContent('Tokyo Station')
    })

    it('asks to close from the close button', async () => {
        const { onClose } = renderSheet(transferItem())

        await userEvent.click(screen.getByRole('button', { name: 'Close' }))

        expect(onClose).toHaveBeenCalled()
    })
})
