import { Drawer } from '@mantine/core'
import { describe, expect, it } from 'vitest'

import type { ItineraryItem } from '@/lib/itemMeta'
import { activityItem, lodgingItem } from '@/test/fixtures'
import { render, screen } from '@/test/render'

import { ItemDetailBody } from './ItemDetailBody'

// ----- setup -----

// The body's title is a Drawer part, which needs a drawer around it.
function renderBody(item: ItineraryItem) {
    render(
        <Drawer.Root opened onClose={() => undefined}>
            <Drawer.Content>
                <ItemDetailBody item={item} />
            </Drawer.Content>
        </Drawer.Root>,
    )
}

// ----- tests -----

describe('ItemDetailBody', () => {
    it('shows the title and labelled fields', () => {
        renderBody(lodgingItem({ title: 'Hotel Sakura' }))

        expect(screen.getByText('Hotel Sakura')).toBeInTheDocument()
        expect(screen.getByText('Nights', { selector: 'dt' })).toBeInTheDocument()
    })

    it('draws the place name with its address', () => {
        renderBody(activityItem({ place: { name: 'Senso-ji', address: '2-3-1 Asakusa' } }))

        expect(screen.getByText('2-3-1 Asakusa')).toBeInTheDocument()
    })

    it('links the source to its host name', () => {
        renderBody(activityItem({ source_url: 'https://www.booking.com/x?y=1' }))

        const link = screen.getByRole('link', { name: /www\.booking\.com/ })
        expect(link).toHaveAttribute('href', 'https://www.booking.com/x?y=1')
    })

    it('drops empty optional fields', () => {
        renderBody(activityItem({ booking_ref: undefined, notes: undefined }))

        expect(screen.queryByText('Booking ref', { selector: 'dt' })).not.toBeInTheDocument()
    })
})
