import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { getTimelineQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import type { TimelineWarning } from '@/api/types.gen'
import { PLAN_ID, sdkOk, timeline, tokyoTimeline } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { ItineraryTimeline } from './ItineraryTimeline'

// ----- setup -----

function renderTimeline() {
    return renderWithQuery(<ItineraryTimeline planId={PLAN_ID} />)
}

const TWO_WARNINGS: TimelineWarning[] = [
    {
        kind: 'no_lodging',
        message: 'No lodging for the night of May 1',
        date: '2027-05-01',
        item_ids: [],
    },
    {
        kind: 'overlap',
        message: 'Hotel Okura overlaps Senso-ji Temple',
        date: '2027-05-03',
        item_ids: ['i1', 'i2'],
    },
]

// ----- tests -----

describe('ItineraryTimeline', () => {
    it('lays out days left to right with the stay spanning its nights', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))

        renderTimeline()

        const days = await screen.findAllByRole('region', { name: /^Day \d/ })
        expect(days).toHaveLength(5)
        expect(days[0]).toHaveTextContent('Outbound')
        expect(screen.getByText('5 days · 4 items')).toBeInTheDocument()

        // Check-in mid day 2 (index 1) → check-out mid day 5 (index 4) on the half-column grid.
        const stay = screen.getByRole('button', { name: 'Hotel Okura, 3 nights' })
        expect(stay).toHaveStyle({ gridColumn: '4 / 10' })
        expect(days[1]).toHaveStyle({ gridColumn: '3 / span 2' })
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

    it('summarizes warnings', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(
            sdkOk({
                ...tokyoTimeline(),
                warnings: [
                    {
                        kind: 'no_lodging',
                        message: 'No lodging for the night of May 1',
                        date: '2027-05-01',
                        item_ids: [],
                    },
                ],
            }),
        )
        renderTimeline()

        await userEvent.click(await screen.findByText('1 thing to check'))

        expect(screen.getByText('No lodging for the night of May 1')).toBeVisible()
        // No onRequestFix: the list is read-only.
        expect(screen.queryByRole('button', { name: /^Fix it/ })).not.toBeInTheDocument()
    })

    it('offers a "Fix it" button per warning that hands the warning over', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(
            sdkOk({ ...tokyoTimeline(), warnings: TWO_WARNINGS }),
        )
        const onRequestFix = vi.fn<(warning: TimelineWarning) => void>()
        renderWithQuery(<ItineraryTimeline planId={PLAN_ID} onRequestFix={onRequestFix} />)

        await userEvent.click(await screen.findByText('2 things to check'))
        expect(screen.getAllByRole('button', { name: /^Fix it: / })).toHaveLength(2)
        await userEvent.click(
            screen.getByRole('button', { name: 'Fix it: Hotel Okura overlaps Senso-ji Temple' }),
        )

        expect(onRequestFix).toHaveBeenCalledExactlyOnceWith(TWO_WARNINGS[1])
    })

    it('disables the "Fix it" buttons when asked', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(
            sdkOk({ ...tokyoTimeline(), warnings: TWO_WARNINGS }),
        )
        renderWithQuery(<ItineraryTimeline planId={PLAN_ID} onRequestFix={vi.fn()} fixDisabled />)

        await userEvent.click(await screen.findByText('2 things to check'))

        for (const button of screen.getAllByRole('button', { name: /^Fix it: / })) {
            expect(button).toBeDisabled()
        }
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
