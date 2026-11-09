import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import type { Issue, TravelPlan } from '@/api/types.gen'
import { issue, planAssessment, sdkOk, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { IssuesPanel } from './IssuesPanel'

// ----- setup -----

const NO_HOTEL = issue({ stage: 'lodging', message: 'a place to stay the night of May 3' })
const OVERLAP = issue({
    code: 'overlap',
    stage: 'activities',
    severity: 'warning',
    message: 'Senso-ji overlaps Tsukiji Market',
})

function reviewWith(issues: Issue[]): TravelPlan {
    return travelPlan({ stage: 'review', assessment: planAssessment({ stage: 'review', issues }) })
}

function renderPanel(plan: TravelPlan, { inline = false, disabled = false } = {}) {
    const user = userEvent.setup()
    const view = renderWithQuery(<IssuesPanel plan={plan} inline={inline} disabled={disabled} />)
    return { user, ...view }
}

// ----- tests -----

describe('IssuesPanel', () => {
    it('renders nothing behind the button when there is nothing to check', () => {
        const { container } = renderPanel(reviewWith([]))

        expect(container).toBeEmptyDOMElement()
    })

    it('lists the issues in a flyout, blocking first', async () => {
        const { user } = renderPanel(reviewWith([OVERLAP, NO_HOTEL]))

        await user.click(screen.getByRole('button', { name: '2 things to check' }))

        const flyout = screen.getByRole('dialog', { name: 'Things to check' })
        const rows = within(flyout).getAllByRole('listitem')
        expect(rows.map((row) => row.textContent)).toEqual([
            'Missing a place to stay the night of May 3Go to Lodging',
            'Senso-ji overlaps Tsukiji Market',
        ])
    })

    it("lists the current stage's missing ride, with nowhere to go to", async () => {
        const noRide = issue({ stage: 'ground', message: 'a transfer from Tower Bridge to hotel' })
        const plan = travelPlan({
            stage: 'ground',
            assessment: planAssessment({ stage: 'ground', issues: [noRide] }),
        })
        const { user } = renderPanel(plan)

        await user.click(screen.getByRole('button', { name: '1 thing to check' }))

        const flyout = screen.getByRole('dialog', { name: 'Things to check' })
        expect(flyout).toHaveTextContent('Missing a transfer from Tower Bridge to hotel')
        expect(within(flyout).queryByRole('button', { name: /^Go to/ })).not.toBeInTheDocument()
    })

    it('goes back to the stage that needs fixing', async () => {
        const changeStage = vi
            .spyOn(sdk, 'changeStage')
            .mockResolvedValue(sdkOk(travelPlan({ stage: 'lodging' })))
        const { user } = renderPanel(reviewWith([NO_HOTEL]))

        await user.click(screen.getByRole('button', { name: '1 thing to check' }))
        await user.click(
            screen.getByRole('button', {
                name: 'Go to Lodging: a place to stay the night of May 3',
            }),
        )

        expect(changeStage.mock.calls[0]?.[0].body).toEqual({ action: 'go_to', stage: 'lodging' })
        // The flyout closes so the stage it moved to is in view.
        expect(screen.queryByRole('dialog', { name: 'Things to check' })).not.toBeInTheDocument()
    })

    it("shows the server's reason when the move is refused", async () => {
        vi.spyOn(sdk, 'changeStage').mockRejectedValue({ detail: 'This plan changed.' })
        const { user } = renderPanel(reviewWith([NO_HOTEL]))

        await user.click(screen.getByRole('button', { name: '1 thing to check' }))
        await user.click(screen.getByRole('button', { name: /^Go to Lodging/ }))

        expect(await screen.findByRole('alert')).toHaveTextContent('This plan changed.')
    })

    it('lists in place at review, with the good news when nothing is left', () => {
        renderPanel(reviewWith([]), { inline: true })

        const section = screen.getByRole('region', { name: 'Before you go' })
        expect(section).toHaveTextContent('Nothing left to fix')
    })

    it('lists in place at review, with Go to buttons', () => {
        renderPanel(reviewWith([NO_HOTEL, OVERLAP]), { inline: true })

        const section = screen.getByRole('region', { name: 'Before you go' })
        expect(section).toHaveTextContent('2 things to check')
        expect(within(section).getByRole('button', { name: /^Go to Lodging/ })).toBeEnabled()
    })

    it('disables Go to while a turn streams', () => {
        renderPanel(reviewWith([NO_HOTEL]), { inline: true, disabled: true })

        expect(screen.getByRole('button', { name: /^Go to Lodging/ })).toBeDisabled()
    })
})
