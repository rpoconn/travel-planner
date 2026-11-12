import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { formatDateRange } from '@/lib/format'
import { sdkOk, timeline, tokyoTimeline, travelPlan } from '@/test/fixtures'
import { renderWithQuery, screen, waitFor } from '@/test/render'

import { TripSummary } from './TripSummary'

// ----- setup -----

const TOKYO_DATES = formatDateRange('2027-05-01', '2027-05-08') ?? ''

// ----- tests -----

describe('TripSummary', () => {
    beforeEach(() => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))
    })

    it('titles the page with the trip, then says where, when and how much is planned', async () => {
        renderWithQuery(<TripSummary plan={travelPlan()} />)

        expect(screen.getByRole('heading', { level: 1, name: 'Tokyo in May' })).toBeInTheDocument()
        expect(
            await screen.findByText(`Tokyo · ${TOKYO_DATES} · 5 days · 4 items`),
        ).toBeInTheDocument()
    })

    it('shows only the title for a trip with nothing filled in yet', async () => {
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(timeline()))
        const blank = travelPlan({ destination: null, start_date: null, end_date: null })

        const { container, queryClient } = renderWithQuery(<TripSummary plan={blank} />)

        expect(screen.getByRole('heading', { level: 1, name: 'Tokyo in May' })).toBeInTheDocument()
        // Once the (empty) timeline has loaded there is still nothing to add.
        await waitFor(() => {
            expect(queryClient.isFetching()).toBe(0)
        })
        expect(container.querySelector('p')).toBeNull()
    })
})
