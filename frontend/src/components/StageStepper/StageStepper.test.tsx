import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import type { TravelPlan } from '@/api/types.gen'
import { sdkOk, stageStatuses, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { StageStepper } from './StageStepper'

// ----- setup -----

function renderStepper(plan: TravelPlan, { disabled = false } = {}) {
    const user = userEvent.setup()
    const result = renderWithQuery(<StageStepper plan={plan} disabled={disabled} />)
    return { ...result, user, nav: screen.getByRole('navigation', { name: 'Planning stages' }) }
}

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

    it('jumps back to an earlier stage from its step', async () => {
        const changeStage = vi
            .spyOn(sdk, 'changeStage')
            .mockResolvedValue(sdkOk(travelPlan({ stage: 'basics' })))
        const { nav, user } = renderStepper(travelPlan({ stage: 'ground' }))

        await user.click(within(nav).getByRole('button', { name: 'Go back to Lodging (done)' }))

        expect(changeStage.mock.calls[0]?.[0].body).toEqual({ action: 'go_to', stage: 'lodging' })
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

    it("shows the server's reason when a stage change is refused", async () => {
        vi.spyOn(sdk, 'changeStage').mockRejectedValue({ detail: 'Not allowed right now.' })
        const { nav, user } = renderStepper(travelPlan({ stage: 'ground' }))

        await user.click(within(nav).getByRole('button', { name: 'Go back to Lodging (done)' }))

        expect(await within(nav).findByRole('alert')).toHaveTextContent('Not allowed right now.')
    })

    it('disables the step buttons while the chat is busy', () => {
        const { nav } = renderStepper(travelPlan({ stage: 'flights' }), { disabled: true })

        for (const button of within(nav).getAllByRole('button')) expect(button).toBeDisabled()
    })
})
