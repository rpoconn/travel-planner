import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import type { TravelPlan } from '@/api/types.gen'
import { planAssessment, sdkOk, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { StageBar } from './StageBar'

// ----- setup -----

function renderBar(plan: TravelPlan, { disabled = false } = {}) {
    const user = userEvent.setup()
    const onContinue = vi.fn<() => void>()
    renderWithQuery(<StageBar plan={plan} disabled={disabled} onContinue={onContinue} />)
    return { user, onContinue }
}

const SEARCHING = planAssessment({
    stage: 'flights',
    next_action: {
        type: 'run_search',
        slot: 'flight_outbound',
        stage: 'flights',
        label: 'Find flights out',
        nights: null,
        gap: null,
    },
})

// ----- tests -----

describe('StageBar', () => {
    it('describes the stage, with no Back or Next at basics', () => {
        renderBar(travelPlan())

        expect(screen.getByText(/Where and when you're going/)).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
        expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
    })

    it('runs a pending search through continue', async () => {
        const { user, onContinue } = renderBar(
            travelPlan({ stage: 'flights', assessment: SEARCHING }),
        )

        await user.click(screen.getByRole('button', { name: 'Find flights out' }))

        expect(onContinue).toHaveBeenCalledOnce()
    })

    it('skips flights and goes back through the stage endpoint', async () => {
        const changeStage = vi
            .spyOn(sdk, 'changeStage')
            .mockResolvedValue(sdkOk(travelPlan({ stage: 'lodging' })))
        const { user } = renderBar(travelPlan({ stage: 'flights', assessment: SEARCHING }))

        await user.click(screen.getByRole('button', { name: 'Skip flights' }))
        await user.click(screen.getByRole('button', { name: 'Back' }))

        expect(changeStage.mock.calls.map((call) => call[0].body)).toEqual([
            { action: 'skip_flights' },
            { action: 'back' },
        ])
    })

    it('celebrates a finished trip', () => {
        renderBar(travelPlan({ stage: 'review' }))

        expect(screen.getByText('Trip complete')).toBeInTheDocument()
    })

    it('disables its buttons while a turn streams', () => {
        renderBar(travelPlan({ stage: 'flights', assessment: SEARCHING }), { disabled: true })

        expect(screen.getByRole('button', { name: 'Find flights out' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled()
    })
})
