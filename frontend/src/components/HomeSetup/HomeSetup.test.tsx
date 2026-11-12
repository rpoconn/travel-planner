import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import { ADA, PHILADELPHIA, sdkOk, tokenResponse } from '@/test/fixtures'
import { renderWithQuery, screen, waitFor } from '@/test/render'

import { HomeSetup } from './HomeSetup'

// ----- setup -----

function renderHome(home = ADA.home) {
    setSessionFromToken(tokenResponse({ ...ADA, home }))
    const user = userEvent.setup()
    renderWithQuery(<HomeSetup />)
    return { user }
}

// ----- tests -----

describe('HomeSetup', () => {
    it('opens by itself when the user has no home, and saves one', async () => {
        const updateMe = vi
            .spyOn(sdk, 'updateMe')
            .mockResolvedValue(sdkOk({ ...ADA, home: PHILADELPHIA }))
        const { user } = renderHome(null)

        const sheet = await screen.findByRole('dialog', { name: 'Where is home?' })
        expect(screen.getByRole('button', { name: 'Set home' })).toBeInTheDocument()
        await user.type(screen.getByLabelText('City or town'), 'Philadelphia')
        await user.type(screen.getByLabelText(/Nearest airport code/), 'phl')
        await user.click(screen.getByRole('button', { name: 'Save home' }))

        expect(updateMe).toHaveBeenCalledWith(
            expect.objectContaining({
                body: {
                    home: {
                        name: 'Philadelphia',
                        city: 'Philadelphia',
                        address: null,
                        iata: 'PHL',
                    },
                },
            }),
        )
        await waitFor(() => {
            expect(sheet).not.toBeInTheDocument()
        })
        // The session (and so the header button) now has the home.
        expect(getSession()?.user.home).toEqual(PHILADELPHIA)
        expect(screen.getByRole('button', { name: 'Home: Philadelphia' })).toBeInTheDocument()
    })

    it('stays open when the app behind the first-run drawer is clicked', async () => {
        const { user } = renderHome(null)

        const sheet = await screen.findByRole('dialog', { name: 'Where is home?' })
        await user.click(document.body)

        expect(sheet).toBeInTheDocument()
    })

    it('stays closed for a user with a home until the button is clicked', async () => {
        const { user } = renderHome(PHILADELPHIA)

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        await user.click(screen.getByRole('button', { name: 'Home: Philadelphia' }))

        expect(await screen.findByRole('dialog', { name: 'Where is home?' })).toBeInTheDocument()
        expect(screen.getByLabelText('City or town')).toHaveValue('Philadelphia')
        expect(screen.getByLabelText(/Nearest airport code/)).toHaveValue('PHL')
    })

    it('rejects an airport code that is not three letters', async () => {
        const updateMe = vi.spyOn(sdk, 'updateMe')
        const { user } = renderHome(null)

        await user.type(await screen.findByLabelText('City or town'), 'Philadelphia')
        await user.type(screen.getByLabelText(/Nearest airport code/), 'P1')

        expect(screen.getByText(/three-letter airport code/)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Save home' })).toBeDisabled()
        expect(updateMe).not.toHaveBeenCalled()
    })

    it('shows the error when saving fails', async () => {
        vi.spyOn(sdk, 'updateMe').mockRejectedValue({ detail: 'Not signed in' })
        const { user } = renderHome(null)

        await user.type(await screen.findByLabelText('City or town'), 'Philadelphia')
        await user.click(screen.getByRole('button', { name: 'Save home' }))

        expect(await screen.findByRole('alert')).toHaveTextContent('Not signed in')
    })
})
