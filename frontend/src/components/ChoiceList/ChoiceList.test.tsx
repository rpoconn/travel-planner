import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { OfferChoice } from '@/api/types.gen'
import { formatDateRange } from '@/lib/format'
import {
    OFFER_ACTIVITIES,
    OFFER_FLIGHTS_OUT,
    OFFER_LODGING,
    OFFER_TRANSFERS,
} from '@/test/fixtures'

import { ChoiceList } from './ChoiceList'

// ----- setup -----

function renderList(action: OfferChoice, { disabled = false } = {}) {
    const user = userEvent.setup()
    const onPick = vi.fn<(ids: string[]) => void>()
    render(<ChoiceList action={action} disabled={disabled} onPick={onPick} />)
    return { user, onPick }
}

// ----- tests -----

describe('ChoiceList', () => {
    it('lists the options under the prompt', () => {
        renderList(OFFER_FLIGHTS_OUT)

        const list = screen.getByRole('region', { name: 'Pick your flight out.' })
        const options = within(list).getAllByRole('article')
        expect(options.map((option) => option.getAttribute('aria-label'))).toEqual([
            'Example Air EX 101',
            'Example Air EX 102',
        ])
    })

    it('sends a single pick at once', async () => {
        const { user, onPick } = renderList(OFFER_FLIGHTS_OUT)

        await user.click(screen.getByRole('button', { name: 'Pick Example Air EX 102' }))

        expect(onPick).toHaveBeenCalledWith(['2'])
    })

    it('collects a multi pick and sends it with one button', async () => {
        const { user, onPick } = renderList(OFFER_ACTIVITIES)
        const send = screen.getByRole('button', { name: 'Pick at least 1' })
        expect(send).toBeDisabled()
        expect(screen.getByText('Pick 1 to 3.')).toBeInTheDocument()

        await user.click(screen.getByRole('button', { name: 'Add Tram 28' }))
        await user.click(screen.getByRole('button', { name: 'Add Tile museum' }))
        await user.click(screen.getByRole('button', { name: 'Add 2 picks' }))

        expect(onPick).toHaveBeenCalledWith(['3', '1'])
    })

    it('can take a toggled option back out', async () => {
        const { user } = renderList(OFFER_ACTIVITIES)

        await user.click(screen.getByRole('button', { name: 'Add Fado night' }))
        await user.click(screen.getByRole('button', { name: 'Added Fado night' }))

        expect(screen.getByRole('button', { name: 'Pick at least 1' })).toBeDisabled()
    })

    it('says which nights a place to stay is for', () => {
        renderList(OFFER_LODGING)

        const list = screen.getByRole('region', { name: 'Pick a place to stay.' })
        const dates = formatDateRange('2027-05-01', '2027-05-03') ?? ''
        expect(within(list).getByText(`2 nights, ${dates}.`)).toBeInTheDocument()
    })

    it('says which gap a way to get there is for', () => {
        renderList(OFFER_TRANSFERS)

        const list = screen.getByRole('region', { name: 'How do you want to get there?' })
        expect(within(list).getByText('From Home to San Francisco International.')).toBeVisible()
    })

    it('disables picking while a turn runs', () => {
        renderList(OFFER_FLIGHTS_OUT, { disabled: true })

        expect(screen.getByRole('button', { name: 'Pick Example Air EX 101' })).toBeDisabled()
    })
})
