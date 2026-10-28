import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getSession } from '@/lib/auth'
import { sdkOk, tokenResponse } from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'

import { RegisterPage } from './RegisterPage'

async function fillAndSubmit() {
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Name (optional)'), 'Ada')
    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
}

describe('RegisterPage', () => {
    it('creates the account and logs the user in', async () => {
        const register = vi.spyOn(sdk, 'register').mockResolvedValue(sdkOk(tokenResponse()))
        const { router } = await renderWithRouter(<RegisterPage />, { path: '/register' })

        await fillAndSubmit()

        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/')
        })
        expect(register).toHaveBeenCalledWith(
            expect.objectContaining({
                body: {
                    email: 'ada@example.com',
                    password: 'correct horse battery',
                    display_name: 'Ada',
                },
            }),
        )
        expect(getSession()?.user.email).toBe('ada@example.com')
    })

    it('shows a conflict when the email is taken', async () => {
        vi.spyOn(sdk, 'register').mockRejectedValue({ detail: 'Email is already registered' })
        await renderWithRouter(<RegisterPage />, { path: '/register' })

        await fillAndSubmit()

        expect(await screen.findByRole('alert')).toHaveTextContent('Email is already registered')
        expect(getSession()).toBeNull()
    })
})
