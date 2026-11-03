import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { PlanStage } from '@/lib/stages'
import { travelPlan } from '@/test/fixtures'

import { StageStep } from './StageStep'

// ----- setup -----

function renderStep(stage: PlanStage, { disabled = false } = {}) {
    const onGoTo = vi.fn()
    const user = userEvent.setup()
    render(
        <ol>
            <StageStep
                plan={travelPlan({ stage: 'lodging' })}
                stage={stage}
                index={1}
                disabled={disabled}
                onGoTo={onGoTo}
            />
        </ol>,
    )
    return { onGoTo, user }
}

// ----- tests -----

describe('StageStep', () => {
    it('marks the current step and renders it as plain text', () => {
        renderStep('lodging')

        expect(screen.getByRole('listitem')).toHaveAttribute('aria-current', 'step')
        expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })

    it('shows locked steps as text with a screen-reader state', () => {
        renderStep('review')

        expect(screen.getByRole('listitem')).toHaveTextContent('(locked)')
        expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })

    it('calls onGoTo with the stage when a passed step is clicked', async () => {
        const { onGoTo, user } = renderStep('basics')

        await user.click(screen.getByRole('button', { name: 'Go back to Basics (done)' }))

        expect(onGoTo).toHaveBeenCalledWith('basics')
    })

    it('disables the passed step button', () => {
        renderStep('basics', { disabled: true })

        expect(screen.getByRole('button', { name: /Go back to Basics/ })).toBeDisabled()
    })
})
