import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import {
    ASK_DESTINATION,
    planAsking,
    planSummary,
    problem,
    sdkOk,
    tokenResponse,
    tokyoTimeline,
    travelPlan,
} from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'
import { sseResponse } from '@/test/sse'

import { WorkspacePage } from './WorkspacePage'

describe('WorkspacePage', () => {
    beforeEach(() => {
        setSessionFromToken(tokenResponse())
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([planSummary()]))
        vi.spyOn(sdk, 'listPlanningMessages').mockResolvedValue(sdkOk([]))
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

    it('loads the selected plan into the planning and itinerary panes', async () => {
        const getPlan = vi
            .spyOn(sdk, 'getPlan')
            .mockResolvedValue(sdkOk(planAsking(ASK_DESTINATION)))

        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)

        expect(await screen.findByRole('heading', { name: 'Tokyo in May' })).toBeInTheDocument()
        expect(getPlan).toHaveBeenCalledWith(
            expect.objectContaining({ path: { plan_id: travelPlan().id } }),
        )
        const chat = screen.getByRole('region', { name: 'Chat' })
        expect(
            await within(chat).findByRole('form', { name: 'Where are you going?' }),
        ).toBeInTheDocument()
        expect(within(chat).getByRole('textbox', { name: 'Message' })).toBeInTheDocument()
        expect(sdk.listPlanningMessages).toHaveBeenCalled()
        const itinerary = screen.getByRole('region', { name: 'Itinerary' })
        expect(await within(itinerary).findAllByRole('region', { name: /^Day \d/ })).toHaveLength(5)
    })

    it('lists what the trip still needs above the panes, not in the itinerary', async () => {
        const plan = travelPlan({ problems: [problem()] })
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(plan))

        await renderWithRouter(<WorkspacePage planId={plan.id} />)

        const needs = await screen.findByRole('region', { name: 'What the trip still needs' })
        expect(needs).toHaveTextContent('1 thing left')
        expect(needs).toHaveTextContent('A place to stay the night of May 3')
        const itinerary = screen.getByRole('region', { name: 'Itinerary' })
        expect(within(itinerary).queryByText('1 thing left')).not.toBeInTheDocument()
        // Ada has no home yet, so the header offers to set one.
        expect(screen.getByRole('button', { name: 'Set home' })).toBeInTheDocument()
    })

    it('asks the planner to fix a problem, in the chat', async () => {
        const plan = travelPlan({ problems: [problem()] })
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(plan))
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan }]))
        await renderWithRouter(<WorkspacePage planId={plan.id} />)
        const chat = screen.getByRole('region', { name: 'Chat' })
        await within(chat).findByRole('textbox', { name: 'Message' })

        await userEvent.click(
            screen.getByRole('button', { name: 'Fix this: a place to stay the night of May 3' }),
        )

        await waitFor(() => {
            expect(fetchSpy).toHaveBeenCalled()
        })
        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.url).toBe(`http://api.test/plans/${plan.id}/planning/continue`)
        expect(await request.json()).toEqual({
            refresh: false,
            problem_id: 'uncovered_night:2027-05-03',
        })
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

    it('hides the trips panel once the trip is worked in', async () => {
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(planAsking(ASK_DESTINATION)))
        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)
        const trips = screen.getByRole('navigation', { name: 'Trips' })
        expect(trips.closest('aside')).not.toHaveAttribute('inert')

        const chat = screen.getByRole('region', { name: 'Chat' })
        await userEvent.click(await within(chat).findByRole('textbox', { name: 'Message' }))

        expect(trips.closest('aside')).toHaveAttribute('inert')
        expect(screen.getByRole('button', { name: 'Show trips' })).toHaveAttribute(
            'aria-expanded',
            'false',
        )
    })

    it('reopens the trips panel from the header, and leaves it open after that', async () => {
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(planAsking(ASK_DESTINATION)))
        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)
        const trips = screen.getByRole('navigation', { name: 'Trips' })
        const chat = screen.getByRole('region', { name: 'Chat' })
        const message = await within(chat).findByRole('textbox', { name: 'Message' })
        await userEvent.click(message)

        await userEvent.click(screen.getByRole('button', { name: 'Show trips' }))
        await userEvent.type(message, 'hello')

        expect(trips.closest('aside')).not.toHaveAttribute('inert')
        expect(screen.getByRole('button', { name: 'Hide trips' })).toBeInTheDocument()
    })

    it('signs out', async () => {
        await renderWithRouter(<WorkspacePage />)

        await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

        expect(getSession()).toBeNull()
    })
})
