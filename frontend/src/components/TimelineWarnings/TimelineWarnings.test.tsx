import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import type { TimelineWarning } from '@/api/types.gen'
import { PLAN_ID, sdkOk, tokyoTimeline } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'

import { TimelineWarnings } from './TimelineWarnings'

// ----- setup -----

const TWO_WARNINGS: TimelineWarning[] = [
    {
        kind: 'no_lodging',
        message: 'No lodging for the night of May 1',
        date: '2027-05-01',
        item_ids: [],
    },
    {
        kind: 'overlap',
        message: 'Hotel Okura overlaps Senso-ji Temple',
        date: '2027-05-03',
        item_ids: ['i1', 'i2'],
    },
]

function mockWarnings(warnings: TimelineWarning[]) {
    return vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk({ ...tokyoTimeline(), warnings }))
}

async function openFlyout(summary: string) {
    await userEvent.click(await screen.findByRole('button', { name: summary }))
    return screen.getByRole('dialog', { name: 'Things to check' })
}

// ----- tests -----

describe('TimelineWarnings', () => {
    it('renders nothing when there is nothing to check', async () => {
        const getTimeline = mockWarnings([])
        const { container } = renderWithQuery(<TimelineWarnings planId={PLAN_ID} />)

        await waitFor(() => {
            expect(getTimeline).toHaveBeenCalled()
        })
        expect(container).toBeEmptyDOMElement()
    })

    it('opens a flyout listing the warnings', async () => {
        mockWarnings(TWO_WARNINGS.slice(0, 1))
        renderWithQuery(<TimelineWarnings planId={PLAN_ID} />)

        const flyout = await openFlyout('1 thing to check')

        expect(within(flyout).getByText('No lodging for the night of May 1')).toBeVisible()
        // No onRequestFix: the list is read-only.
        expect(within(flyout).queryByRole('button', { name: /^Fix it/ })).not.toBeInTheDocument()

        await userEvent.keyboard('{Escape}')
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
    })

    it('offers a "Fix it" button per warning that hands the warning over and closes', async () => {
        mockWarnings(TWO_WARNINGS)
        const onRequestFix = vi.fn<(warning: TimelineWarning) => void>()
        renderWithQuery(<TimelineWarnings planId={PLAN_ID} onRequestFix={onRequestFix} />)

        const flyout = await openFlyout('2 things to check')
        expect(within(flyout).getAllByRole('button', { name: /^Fix it: / })).toHaveLength(2)
        await userEvent.click(
            within(flyout).getByRole('button', {
                name: 'Fix it: Hotel Okura overlaps Senso-ji Temple',
            }),
        )

        expect(onRequestFix).toHaveBeenCalledExactlyOnceWith(TWO_WARNINGS[1])
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
    })

    it('disables the "Fix it" buttons when asked', async () => {
        mockWarnings(TWO_WARNINGS)
        renderWithQuery(<TimelineWarnings planId={PLAN_ID} onRequestFix={vi.fn()} fixDisabled />)

        const flyout = await openFlyout('2 things to check')

        for (const button of within(flyout).getAllByRole('button', { name: /^Fix it: / })) {
            expect(button).toBeDisabled()
        }
    })
})
