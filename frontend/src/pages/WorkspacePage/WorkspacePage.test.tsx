import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import {
    ASK_DESTINATION,
    issue,
    planAsking,
    planAssessment,
    planSummary,
    sdkOk,
    tokenResponse,
    tokyoTimeline,
    travelPlan,
} from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'

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

    it("lists the planner's issues in the stage bar, not the itinerary", async () => {
        const plan = travelPlan({
            stage: 'activities',
            assessment: planAssessment({ stage: 'activities', issues: [issue()] }),
        })
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(plan))

        await renderWithRouter(<WorkspacePage planId={plan.id} />)

        const trigger = await screen.findByRole('button', { name: '1 thing to check' })
        const itinerary = screen.getByRole('region', { name: 'Itinerary' })
        expect(within(itinerary).queryByText('1 thing to check')).not.toBeInTheDocument()
        await userEvent.click(trigger)
        expect(screen.getByRole('dialog', { name: 'Things to check' })).toHaveTextContent(
            'a place to stay the night of May 3',
        )
    })

    it('shows the stage stepper for the selected plan and the home button', async () => {
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(sdkOk(travelPlan({ stage: 'flights' })))

        await renderWithRouter(<WorkspacePage planId={travelPlan().id} />)

        const stepper = await screen.findByRole('navigation', { name: 'Planning stages' })
        expect(within(stepper).getByRole('listitem', { current: 'step' })).toHaveTextContent(
            'Flights',
        )
        // Back / Skip flights are separate from the steps, in the planning panel's stage bar.
        const controls = await screen.findByRole('region', { name: 'Stage controls' })
        expect(within(controls).getByRole('button', { name: 'Skip flights' })).toBeEnabled()
        // Ada has no home yet, so the header offers to set one.
        expect(screen.getByRole('button', { name: 'Set home' })).toBeInTheDocument()
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
