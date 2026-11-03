import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { getPlanQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import type { TravelPlan } from '@/api/types.gen'
import { PLAN_ID, sdkOk, stageStatuses, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { StageStepper } from './StageStepper'

// ----- setup -----

function renderStepper(plan: TravelPlan, { disabled = false } = {}) {
    const user = userEvent.setup()
    const result = renderWithQuery(<StageStepper plan={plan} disabled={disabled} />)
    return { ...result, user, nav: screen.getByRole('navigation', { name: 'Planning stages' }) }
}

function mockChangeStage(result: TravelPlan) {
    return vi.spyOn(sdk, 'changeStage').mockResolvedValue(sdkOk(result))
}

const sentBody = (spy: ReturnType<typeof mockChangeStage>) => spy.mock.calls[0]?.[0].body

// ----- tests -----

describe('StageStepper', () => {
    it('shows six steps with the current one marked and later ones locked', () => {
        const { nav } = renderStepper(travelPlan({ stage: 'lodging' }))

        const steps = within(nav).getAllByRole('listitem')
        expect(steps.map((step) => step.textContent)).toEqual([
            expect.stringContaining('Basics'),
            expect.stringContaining('Flights'),
            expect.stringContaining('Lodging'),
            expect.stringContaining('Activities'),
            expect.stringContaining('Transfers'),
            expect.stringContaining('Review'),
        ])
        expect(steps[2]).toHaveAttribute('aria-current', 'step')
        expect(steps[3]).toHaveTextContent('(locked)')
        expect(within(nav).getByRole('button', { name: 'Go back to Basics (done)' })).toBeEnabled()
    })

    it('disables Next and lists what is missing until the stage is ready', () => {
        const { nav } = renderStepper(
            travelPlan({
                stage: 'flights',
                stages: stageStatuses({ flights: ['a flight out', 'a flight home'] }),
            }),
        )

        expect(within(nav).getByRole('button', { name: 'Next' })).toBeDisabled()
        expect(within(nav).getByTestId('stage-missing')).toHaveTextContent(
            'Missing: a flight out, a flight home',
        )
    })

    it('moves to the next stage and caches the plan the server returns', async () => {
        const next = travelPlan({ stage: 'flights', version: 2 })
        const changeStage = mockChangeStage(next)
        const { nav, user, queryClient } = renderStepper(travelPlan())

        // Back is hidden in the first stage.
        expect(within(nav).queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
        await user.click(within(nav).getByRole('button', { name: 'Next' }))

        expect(changeStage).toHaveBeenCalledWith(
            expect.objectContaining({ path: { plan_id: PLAN_ID }, body: { action: 'next' } }),
        )
        await waitFor(() => {
            expect(queryClient.getQueryData(getPlanQueryKey({ path: { plan_id: PLAN_ID } }))).toBe(
                next,
            )
        })
    })

    it('skips flights and goes back from the flights stage', async () => {
        const changeStage = mockChangeStage(travelPlan({ stage: 'lodging' }))
        const { nav, user } = renderStepper(
            travelPlan({ stage: 'flights', stages: stageStatuses({ flights: ['a flight out'] }) }),
        )

        await user.click(within(nav).getByRole('button', { name: 'Skip flights' }))
        expect(sentBody(changeStage)).toEqual({ action: 'skip_flights' })

        changeStage.mockClear()
        await user.click(within(nav).getByRole('button', { name: 'Back' }))
        expect(sentBody(changeStage)).toEqual({ action: 'back' })
    })

    it('jumps back to an earlier stage from its step', async () => {
        const changeStage = mockChangeStage(travelPlan({ stage: 'basics' }))
        const { nav, user } = renderStepper(travelPlan({ stage: 'ground' }))

        await user.click(within(nav).getByRole('button', { name: 'Go back to Lodging (done)' }))

        expect(sentBody(changeStage)).toEqual({ action: 'go_to', stage: 'lodging' })
    })

    it('flags a passed stage that is no longer complete, and skipped flights', () => {
        const { nav } = renderStepper(
            travelPlan({
                stage: 'activities',
                flights_skipped: true,
                stages: stageStatuses({ lodging: ['a place to stay the night of May 2'] }),
            }),
        )

        expect(
            within(nav).getByRole('button', { name: 'Go back to Lodging (needs attention)' }),
        ).toHaveAttribute('title', 'Missing: a place to stay the night of May 2')
        expect(within(nav).getAllByRole('listitem')[1]).toHaveTextContent('Flights (skipped)')
    })

    it('shows "Trip complete" at review instead of Next', () => {
        const { nav } = renderStepper(travelPlan({ stage: 'review' }))

        expect(within(nav).getByText('Trip complete')).toBeInTheDocument()
        expect(within(nav).queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
        expect(within(nav).getByRole('button', { name: 'Back' })).toBeEnabled()
    })

    it("shows the server's reason when a stage change is refused", async () => {
        vi.spyOn(sdk, 'changeStage').mockRejectedValue({
            detail: 'Finish the ground transport stage first: a transfer from Home (Philadelphia) to PHL.',
        })
        const { nav, user } = renderStepper(travelPlan({ stage: 'ground' }))

        await user.click(within(nav).getByRole('button', { name: 'Next' }))

        expect(await within(nav).findByRole('alert')).toHaveTextContent(
            'Finish the ground transport stage first',
        )
    })

    it('disables every button while the chat is busy', () => {
        const { nav } = renderStepper(travelPlan({ stage: 'flights' }), { disabled: true })

        for (const button of within(nav).getAllByRole('button')) expect(button).toBeDisabled()
    })
})
