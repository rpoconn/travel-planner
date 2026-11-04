import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import {
    ASK_DESTINATION,
    planAsking,
    planSummary,
    sdkOk,
    tokenResponse,
    tokyoTimeline,
    travelPlan,
} from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'
import { controlledSseResponse } from '@/test/sse'

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

    it('sends a "Fix it" from the stage bar warnings through the chat', async () => {
        const warning = {
            kind: 'no_lodging' as const,
            message: 'No lodging for the night of May 1',
            date: '2027-05-01',
            item_ids: [],
        }
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(travelPlan()))
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(
            sdkOk({ ...tokyoTimeline(), warnings: [warning] }),
        )
        const stream = controlledSseResponse()
        const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)
        const chat = screen.getByRole('region', { name: 'Chat' })
        await within(chat).findByText('Where to next?')

        // The warnings moved out of the itinerary, into the stage bar.
        const itinerary = screen.getByRole('region', { name: 'Itinerary' })
        expect(within(itinerary).queryByText('1 thing to check')).not.toBeInTheDocument()
        const trigger = await screen.findByRole('button', { name: '1 thing to check' })
        await userEvent.click(trigger)
        await userEvent.click(screen.getByRole('button', { name: `Fix it: ${warning.message}` }))

        expect(within(chat).getByText(`Fix: ${warning.message}`)).toBeInTheDocument()
        // The flyout closes, and its buttons are disabled while the reply streams.
        await waitFor(() => {
            expect(
                screen.queryByRole('dialog', { name: 'Things to check' }),
            ).not.toBeInTheDocument()
        })
        await userEvent.click(trigger)
        expect(screen.getByRole('button', { name: `Fix it: ${warning.message}` })).toBeDisabled()
        await waitFor(() => {
            expect(fetchSpy).toHaveBeenCalledTimes(1)
        })
        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(await request.json()).toEqual({ content: `Fix: ${warning.message}`, fix: warning })
        stream.close()
        await waitFor(() => {
            expect(screen.getByRole('button', { name: `Fix it: ${warning.message}` })).toBeEnabled()
        })
    })

    it('shows the stage stepper for the selected plan and the home button', async () => {
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(travelPlan({ stage: 'flights' })))

        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)

        const stepper = await screen.findByRole('navigation', { name: 'Planning stages' })
        expect(within(stepper).getByRole('listitem', { current: 'step' })).toHaveTextContent(
            'Flights',
        )
        // The Next / Skip buttons are separate from the steps, inline in the chat transcript.
        const controls = await screen.findByRole('region', { name: 'Stage controls' })
        expect(within(controls).getByRole('button', { name: 'Skip flights' })).toBeEnabled()
        // Ada has no home yet, so the header offers to set one.
        expect(screen.getByRole('button', { name: 'Set home' })).toBeInTheDocument()
    })

    it('runs the planning flow instead of the chat when the switch is on', async () => {
        vi.stubEnv('VITE_PLANNING_ENGINE', 'true')
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(planAsking(ASK_DESTINATION)))
        const transcript = vi.spyOn(sdk, 'listPlanningMessages').mockResolvedValue(sdkOk([]))

        try {
            await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)

            const chat = screen.getByRole('region', { name: 'Chat' })
            expect(
                await within(chat).findByRole('form', { name: 'Where are you going?' }),
            ).toBeInTheDocument()
            expect(transcript).toHaveBeenCalled()
            expect(sdk.listMessages).not.toHaveBeenCalled()
        } finally {
            vi.unstubAllEnvs()
        }
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
