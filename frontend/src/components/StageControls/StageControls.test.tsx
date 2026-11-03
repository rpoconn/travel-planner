import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { getPlanQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import type { TravelPlan } from '@/api/types.gen'
import { PLAN_ID, sdkOk, stageStatuses, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { StageControls } from './StageControls'

// ----- setup -----

function renderControls(plan: TravelPlan, { disabled = false } = {}) {
    const user = userEvent.setup()
    const onAsk = vi.fn<(message: string) => void>()
    const result = renderWithQuery(<StageControls plan={plan} disabled={disabled} onAsk={onAsk} />)
    return {
        ...result,
        user,
        onAsk,
        region: screen.getByRole('region', { name: 'Stage controls' }),
    }
}

function mockChangeStage(result: TravelPlan) {
    return vi.spyOn(sdk, 'changeStage').mockResolvedValue(sdkOk(result))
}

const sentBody = (spy: ReturnType<typeof mockChangeStage>) => spy.mock.calls[0]?.[0].body

// ----- tests -----

describe('StageControls', () => {
    it('disables Next and lists what is missing until the stage is ready', () => {
        const { region } = renderControls(
            travelPlan({
                stage: 'flights',
                stages: stageStatuses({ flights: ['a flight out', 'a flight home'] }),
            }),
        )

        expect(within(region).getByRole('button', { name: 'Next' })).toBeDisabled()
        expect(within(region).getByTestId('stage-missing')).toHaveTextContent(
            'Missing: a flight out, a flight home',
        )
    })

    it('asks the assistant for the next step while the stage is incomplete', async () => {
        const { region, user, onAsk } = renderControls(
            travelPlan({
                stage: 'flights',
                stages: stageStatuses({ flights: ['a flight out', 'a flight home'] }),
            }),
        )

        await user.click(within(region).getByRole('button', { name: 'Find flights' }))

        expect(onAsk).toHaveBeenCalledWith(
            'Find flights for the trip to Tokyo. Still needed: a flight out, a flight home.',
        )
    })

    it('has no next-step button once the stage is ready; Next is the action', () => {
        const { region } = renderControls(travelPlan({ stage: 'lodging' }))

        expect(
            within(region)
                .getAllByRole('button')
                .map((b) => b.textContent),
        ).toEqual(['Back', 'Next'])
        expect(within(region).getByRole('button', { name: 'Next' })).toBeEnabled()
    })

    it('moves to the next stage and caches the plan the server returns', async () => {
        const next = travelPlan({ stage: 'flights', version: 2 })
        const changeStage = mockChangeStage(next)
        const { region, user, queryClient } = renderControls(travelPlan())

        // Back is hidden in the first stage.
        expect(within(region).queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
        await user.click(within(region).getByRole('button', { name: 'Next' }))

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
        const { region, user } = renderControls(
            travelPlan({ stage: 'flights', stages: stageStatuses({ flights: ['a flight out'] }) }),
        )

        await user.click(within(region).getByRole('button', { name: 'Skip flights' }))
        expect(sentBody(changeStage)).toEqual({ action: 'skip_flights' })

        changeStage.mockClear()
        await user.click(within(region).getByRole('button', { name: 'Back' }))
        expect(sentBody(changeStage)).toEqual({ action: 'back' })
    })

    it('shows "Trip complete" at review instead of Next', () => {
        const { region } = renderControls(travelPlan({ stage: 'review' }))

        expect(within(region).getByText('Trip complete')).toBeInTheDocument()
        expect(within(region).queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
        expect(within(region).getByRole('button', { name: 'Back' })).toBeEnabled()
    })

    it("shows the server's reason when a stage change is refused", async () => {
        vi.spyOn(sdk, 'changeStage').mockRejectedValue({
            detail: 'Finish the ground transport stage first: a transfer from Home (Philadelphia) to PHL.',
        })
        const { region, user } = renderControls(travelPlan({ stage: 'ground' }))

        await user.click(within(region).getByRole('button', { name: 'Next' }))

        expect(await within(region).findByRole('alert')).toHaveTextContent(
            'Finish the ground transport stage first',
        )
    })

    it('disables every button while the chat is busy', () => {
        const { region } = renderControls(travelPlan({ stage: 'flights' }), { disabled: true })

        for (const button of within(region).getAllByRole('button')) expect(button).toBeDisabled()
    })
})
