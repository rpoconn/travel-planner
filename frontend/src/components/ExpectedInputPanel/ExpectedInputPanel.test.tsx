import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { AskInput } from '@/api/types.gen'
import { ASK_BUDGET, ASK_DATES, ASK_DESTINATION, ASK_FLIGHT_PREFERENCES } from '@/test/fixtures'

import { ExpectedInputPanel } from './ExpectedInputPanel'

// ----- setup -----

function renderPanel(action: AskInput, { disabled = false } = {}) {
    const user = userEvent.setup()
    const onSubmit = vi.fn<(value: Record<string, unknown>) => void>()
    const onSkip = vi.fn<() => void>()
    render(
        <ExpectedInputPanel
            action={action}
            disabled={disabled}
            onSubmit={onSubmit}
            onSkip={onSkip}
        />,
    )
    return { user, onSubmit, onSkip, form: screen.getByRole('form', { name: action.prompt }) }
}

// ----- tests -----

describe('ExpectedInputPanel', () => {
    it('asks the question and submits a place by name', async () => {
        const { user, onSubmit } = renderPanel(ASK_DESTINATION)

        expect(screen.getByText('Where are you going?')).toBeInTheDocument()
        await user.type(screen.getByRole('textbox', { name: 'Destination' }), 'Lisbon')
        await user.click(screen.getByRole('button', { name: 'Continue' }))

        expect(onSubmit).toHaveBeenCalledWith({ destination: { name: 'Lisbon' } })
    })

    it('says what is wrong instead of submitting', async () => {
        const { user, onSubmit } = renderPanel(ASK_DESTINATION)

        await user.click(screen.getByRole('button', { name: 'Continue' }))

        expect(screen.getByRole('alert')).toHaveTextContent('Destination is required.')
        expect(onSubmit).not.toHaveBeenCalled()
    })

    it('submits a date range', async () => {
        const { user, onSubmit } = renderPanel(ASK_DATES)

        // jsdom's date inputs don't take typing, so set their values directly.
        fireEvent.change(screen.getByLabelText('Trip dates: from'), {
            target: { value: '2027-05-01' },
        })
        fireEvent.change(screen.getByLabelText('Trip dates: to'), {
            target: { value: '2027-05-08' },
        })
        await user.click(screen.getByRole('button', { name: 'Continue' }))

        expect(onSubmit).toHaveBeenCalledWith({ dates: { start: '2027-05-01', end: '2027-05-08' } })
        expect(screen.getByLabelText('Trip dates: to')).toHaveAttribute('min', '2027-05-01')
    })

    it('picks options from choice chips', async () => {
        const { user, onSubmit } = renderPanel(ASK_FLIGHT_PREFERENCES)
        const cabin = screen.getByRole('radiogroup', { name: 'Cabin' })

        expect(screen.getByRole('radio', { name: 'Economy' })).toBeChecked()
        expect(screen.getByRole('radio', { name: 'Any' })).toBeChecked()
        await user.click(screen.getByRole('radio', { name: 'Business' }))
        await user.click(screen.getByRole('radio', { name: 'Nonstop' }))
        await user.click(screen.getByRole('button', { name: 'Continue' }))

        expect(cabin).toBeInTheDocument()
        expect(onSubmit).toHaveBeenCalledWith({ cabin: 'business', max_stops: 0 })
    })

    it('offers Skip only for an optional question', async () => {
        const { user, onSkip } = renderPanel(ASK_BUDGET)

        await user.click(screen.getByRole('button', { name: 'Skip' }))

        expect(onSkip).toHaveBeenCalledOnce()
    })

    it('has no Skip for a required question, and locks while disabled', () => {
        renderPanel(ASK_DESTINATION, { disabled: true })

        expect(screen.queryByRole('button', { name: 'Skip' })).not.toBeInTheDocument()
        expect(screen.getByRole('textbox', { name: 'Destination' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
    })

    it('starts from what the plan already holds', () => {
        renderPanel({ ...ASK_BUDGET, prefill: { budget: { amount: '4000', currency: 'EUR' } } })

        expect(screen.getByRole('spinbutton', { name: 'Total budget' })).toHaveValue(4000)
        expect(screen.getByRole('textbox', { name: 'Total budget: currency' })).toHaveValue('EUR')
    })
})
