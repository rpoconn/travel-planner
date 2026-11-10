import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { planSummary, sdkOk, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { PlansSidebar } from './PlansSidebar'

// ----- setup -----

const TOKYO = planSummary()
const LISBON = planSummary({
    id: 'aaaaaaaa-0000-0000-0000-000000000002',
    title: 'Lisbon weekend',
    destination: null,
    start_date: null,
    end_date: null,
})

function renderSidebar(selectedPlanId?: string) {
    const onSelectPlan = vi.fn()
    const onPlanDeleted = vi.fn()
    renderWithQuery(
        <PlansSidebar
            selectedPlanId={selectedPlanId}
            onSelectPlan={onSelectPlan}
            onPlanDeleted={onPlanDeleted}
        />,
    )
    return { onSelectPlan, onPlanDeleted, user: userEvent.setup() }
}

// ----- tests -----

describe('PlansSidebar', () => {
    it('lists plans with a subtitle and marks the selected one', async () => {
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([TOKYO, LISBON]))

        renderSidebar(TOKYO.id)

        const tokyo = await screen.findByRole('button', { name: /^Tokyo in May/ })
        expect(tokyo).toHaveAttribute('aria-current', 'page')
        expect(tokyo).toHaveTextContent('Tokyo ·')
        expect(screen.getByRole('button', { name: /^Lisbon weekend/ })).toHaveTextContent(
            'No dates yet',
        )
    })

    it('shows an empty state', async () => {
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([]))

        renderSidebar()

        expect(await screen.findByText(/No trips yet/)).toBeInTheDocument()
    })

    it('selects a plan on click', async () => {
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([TOKYO]))
        const { onSelectPlan, user } = renderSidebar()

        await user.click(await screen.findByRole('button', { name: /^Tokyo in May/ }))

        expect(onSelectPlan).toHaveBeenCalledWith(TOKYO.id)
    })

    it('creates a plan, selects it, and opens it for renaming', async () => {
        const created = planSummary({ id: 'new-plan', title: 'Untitled trip' })
        vi.spyOn(sdk, 'listPlans')
            .mockResolvedValueOnce(sdkOk([]))
            .mockResolvedValue(sdkOk([created]))
        const createPlan = vi
            .spyOn(sdk, 'createPlan')
            .mockResolvedValue(sdkOk(travelPlan({ id: 'new-plan', title: 'Untitled trip' })))
        const { onSelectPlan, user } = renderSidebar()

        await user.click(await screen.findByRole('button', { name: 'New trip' }))

        expect(await screen.findByRole('textbox', { name: 'Trip name' })).toHaveValue(
            'Untitled trip',
        )
        expect(createPlan).toHaveBeenCalledWith(
            expect.objectContaining({ body: { title: 'Untitled trip' } }),
        )
        expect(onSelectPlan).toHaveBeenCalledWith('new-plan')
    })

    it('renames a plan on Enter', async () => {
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([TOKYO]))
        const updatePlan = vi
            .spyOn(sdk, 'updatePlan')
            .mockResolvedValue(sdkOk(travelPlan({ title: 'Tokyo & Kyoto' })))
        const { user } = renderSidebar()

        await user.click(await screen.findByRole('button', { name: 'Rename Tokyo in May' }))
        const input = screen.getByRole('textbox', { name: 'Trip name' })
        await user.clear(input)
        await user.type(input, 'Tokyo & Kyoto{Enter}')

        await waitFor(() => {
            expect(updatePlan).toHaveBeenCalledTimes(1)
        })
        expect(updatePlan).toHaveBeenCalledWith(
            expect.objectContaining({
                path: { plan_id: TOKYO.id },
                body: { title: 'Tokyo & Kyoto' },
            }),
        )
        expect(screen.queryByRole('textbox', { name: 'Trip name' })).not.toBeInTheDocument()
    })

    it('cancels a rename on Escape', async () => {
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([TOKYO]))
        const updatePlan = vi.spyOn(sdk, 'updatePlan')
        const { user } = renderSidebar()

        await user.click(await screen.findByRole('button', { name: 'Rename Tokyo in May' }))
        await user.type(screen.getByRole('textbox', { name: 'Trip name' }), 'Nope{Escape}')

        expect(screen.queryByRole('textbox', { name: 'Trip name' })).not.toBeInTheDocument()
        expect(updatePlan).not.toHaveBeenCalled()
    })

    it('deletes a plan after confirmation', async () => {
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([TOKYO]))
        const deletePlan = vi.spyOn(sdk, 'deletePlan').mockResolvedValue(sdkOk(undefined))
        const { onPlanDeleted, user } = renderSidebar(TOKYO.id)

        await user.click(await screen.findByRole('button', { name: 'Delete Tokyo in May' }))
        const dialog = await screen.findByRole('alertdialog')
        expect(dialog).toHaveTextContent('Delete “Tokyo in May”?')
        await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

        await waitFor(() => {
            expect(onPlanDeleted).toHaveBeenCalledWith(TOKYO.id)
        })
        expect(deletePlan).toHaveBeenCalledWith(
            expect.objectContaining({ path: { plan_id: TOKYO.id } }),
        )
        await waitFor(() => {
            expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
        })
    })
})
