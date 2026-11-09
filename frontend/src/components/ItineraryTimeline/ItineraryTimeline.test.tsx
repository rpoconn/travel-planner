import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { getTimelineQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import { gap, PHILADELPHIA, PLAN_ID, sdkOk, timeline, tokyoTimeline } from '@/test/fixtures'
import type { ItineraryItem } from '@/lib/itemMeta'
import { renderWithQuery } from '@/test/render'

import { ItineraryTimeline } from './ItineraryTimeline'

// ----- setup -----

function renderTimeline(onRemoveItem?: (item: ItineraryItem) => void) {
    return renderWithQuery(<ItineraryTimeline planId={PLAN_ID} onRemoveItem={onRemoveItem} />)
}

// ----- tests -----

describe('ItineraryTimeline', () => {
    it('lays out days left to right with the stay spanning its nights', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))

        renderTimeline()

        const days = await screen.findAllByRole('region', { name: /^Day \d/ })
        expect(days).toHaveLength(5)
        expect(days[0]).toHaveTextContent('Outbound')

        // Check-in mid day 2 (index 1) → check-out mid day 5 (index 4) on the half-column grid.
        const stay = screen.getByRole('button', { name: 'Hotel Okura, 3 nights' })
        expect(stay).toHaveStyle({ gridColumn: '4 / 10' })
        expect(days[1]).toHaveStyle({ gridColumn: '3 / span 2' })
    })

    it('draws home at both ends and the missing way from home to the first flight', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(
            sdkOk({
                ...tokyoTimeline(),
                home: PHILADELPHIA,
                gaps: [gap({ from_place: PHILADELPHIA })],
            }),
        )

        renderTimeline()

        const first = await screen.findByRole('region', { name: /^Day 1,/ })
        const last = screen.getByRole('region', { name: /^Day 5,/ })
        expect(within(first).getAllByRole('listitem')[0]).toHaveTextContent('Home · Philadelphia')
        expect(first).toHaveTextContent(
            'No transfer from Home (Philadelphia) to San Francisco International',
        )
        expect(within(last).getAllByRole('listitem').at(-1)).toHaveTextContent(
            'Home · Philadelphia',
        )
        expect(screen.getAllByTestId('home-chip')).toHaveLength(2)
    })

    it('opens the detail sheet for a clicked item and closes it', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))
        renderTimeline()

        await userEvent.click(await screen.findByRole('button', { name: /Senso-ji Temple/ }))

        const dialog = screen.getByRole('dialog', { name: 'Senso-ji Temple' })
        expect(dialog).toHaveTextContent('Sightseeing')

        await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
    })

    it('removes an item from its sheet, closing the sheet', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))
        const onRemoveItem = vi.fn()
        renderTimeline(onRemoveItem)

        await userEvent.click(await screen.findByRole('button', { name: /Senso-ji Temple/ }))
        await userEvent.click(screen.getByRole('button', { name: 'Remove from trip' }))
        await userEvent.click(screen.getByRole('button', { name: 'Remove' }))

        expect(onRemoveItem).toHaveBeenCalledWith(
            expect.objectContaining({ title: 'Senso-ji Temple' }),
        )
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
    })

    it('has no Remove button when nothing handles it', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))
        renderTimeline()

        await userEvent.click(await screen.findByRole('button', { name: /Senso-ji Temple/ }))

        expect(screen.queryByRole('button', { name: 'Remove from trip' })).not.toBeInTheDocument()
    })

    it('closes the sheet when a refetch no longer has the item', async () => {
        const getTimeline = vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))
        const { queryClient } = renderTimeline()
        await userEvent.click(await screen.findByRole('button', { name: /Senso-ji Temple/ }))
        expect(screen.getByRole('dialog')).toBeInTheDocument()

        // The assistant removed the activity: the plan_updated handler invalidates the query.
        const withoutActivity = tokyoTimeline()
        withoutActivity.days[2] = {
            index: 2,
            date: '2027-05-03',
            entries: [],
            timezone: 'Asia/Tokyo',
        }
        getTimeline.mockResolvedValue(sdkOk(withoutActivity))
        await queryClient.invalidateQueries({
            queryKey: getTimelineQueryKey({ path: { plan_id: PLAN_ID } }),
        })

        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
    })

    it('shows an empty state when the trip has no dates or items', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(timeline()))

        renderTimeline()

        expect(await screen.findByText('No itinerary yet')).toBeInTheDocument()
    })

    it('nudges toward the chat when the days are all empty', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(
            sdkOk(
                timeline({ days: [{ index: 0, date: '2027-05-01', entries: [], timezone: null }] }),
            ),
        )

        renderTimeline()

        expect(await screen.findByText(/Nothing planned yet/)).toBeInTheDocument()
    })

    it('shows load errors', async () => {
        vi.spyOn(sdk, 'getTimeline').mockRejectedValue({ detail: 'Plan not found' })

        renderTimeline()

        expect(await screen.findByRole('alert')).toHaveTextContent('Plan not found')
    })
})
