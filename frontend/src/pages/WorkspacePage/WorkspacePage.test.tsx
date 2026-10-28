import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import { planSummary, sdkOk, tokenResponse, travelPlan } from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'

import { WorkspacePage } from './WorkspacePage'

describe('WorkspacePage', () => {
    beforeEach(() => {
        setSessionFromToken(tokenResponse())
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([planSummary()]))
    })

    it('shows the three panes and the signed-in user', async () => {
        await renderWithRouter(<WorkspacePage />)

        expect(screen.getByRole('navigation', { name: 'Trips' })).toBeInTheDocument()
        expect(screen.getByRole('region', { name: 'Chat' })).toHaveTextContent(
            'Pick a trip to start',
        )
        expect(screen.getByRole('region', { name: 'Itinerary' })).toBeInTheDocument()
        expect(screen.getByTestId('current-user')).toHaveTextContent('Ada')
    })

    it('loads and shows the selected plan', async () => {
        const getPlan = vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(travelPlan()))

        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)

        expect(await screen.findByRole('heading', { name: 'Tokyo in May' })).toBeInTheDocument()
        expect(getPlan).toHaveBeenCalledWith(
            expect.objectContaining({ path: { plan_id: travelPlan().id } }),
        )
    })

    it('shows an error when the plan cannot be loaded', async () => {
        vi.spyOn(sdk, 'getPlan').mockRejectedValue({ detail: 'Plan not found' })

        await renderWithRouter(<WorkspacePage planId="missing" />)

        expect(await screen.findByRole('alert')).toHaveTextContent('Plan not found')
    })

    it('navigates to a plan picked in the sidebar', async () => {
        const { router } = await renderWithRouter(<WorkspacePage />)

        await userEvent.click(await screen.findByRole('button', { name: /^Tokyo in May/ }))

        await waitFor(() => {
            expect(router.state.location.pathname).toBe(`/plans/${planSummary().id}`)
        })
    })

    it('signs out', async () => {
        await renderWithRouter(<WorkspacePage />)

        await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

        expect(getSession()).toBeNull()
    })
})
