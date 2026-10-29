import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import { planSummary, sdkOk, tokenResponse, tokyoTimeline, travelPlan } from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'

import { WorkspacePage } from './WorkspacePage'

describe('WorkspacePage', () => {
    beforeEach(() => {
        setSessionFromToken(tokenResponse())
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([planSummary()]))
        vi.spyOn(sdk, 'listMessages').mockResolvedValue(sdkOk([]))
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(tokyoTimeline()))
    })

    it('shows the three panes and the signed-in user', async () => {
        await renderWithRouter(<WorkspacePage />)

        expect(screen.getByRole('navigation', { name: 'Trips' })).toBeInTheDocument()
        expect(screen.getByRole('region', { name: 'Chat' })).toHaveTextContent(
            'Pick a trip to start',
        )
        expect(screen.getByRole('region', { name: 'Itinerary' })).toHaveTextContent(
            'Select a trip to see its itinerary.',
        )
        expect(screen.getByTestId('current-user')).toHaveTextContent('Ada')
        expect(screen.getByRole('button', { name: /Switch to (dark|light) theme/ })).toBeVisible()
    })

    it('loads the selected plan into the chat and itinerary panes', async () => {
        const getPlan = vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(travelPlan()))

        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)

        expect(await screen.findByRole('heading', { name: 'Tokyo in May' })).toBeInTheDocument()
        expect(getPlan).toHaveBeenCalledWith(
            expect.objectContaining({ path: { plan_id: travelPlan().id } }),
        )
        const chat = screen.getByRole('region', { name: 'Chat' })
        expect(await within(chat).findByText('Where to next?')).toBeInTheDocument()
        expect(within(chat).getByRole('textbox', { name: 'Message' })).toBeInTheDocument()
        const itinerary = screen.getByRole('region', { name: 'Itinerary' })
        expect(await within(itinerary).findAllByRole('region', { name: /^Day \d/ })).toHaveLength(5)
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
