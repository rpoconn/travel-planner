import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { getToken } from '@/lib/auth'
import { sdkOk, tokenResponse } from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'

import { LoginPage } from './LoginPage'

async function fillAndSubmit() {
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('LoginPage', () => {
    it('signs in, stores the session, and goes to the workspace', async () => {
        const login = vi.spyOn(sdk, 'login').mockResolvedValue(sdkOk(tokenResponse()))
        const { router } = await renderWithRouter(<LoginPage />, { path: '/login' })

        await fillAndSubmit()

        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/')
        })
        expect(login).toHaveBeenCalledWith(
            expect.objectContaining({
                body: { email: 'ada@example.com', password: 'correct horse battery' },
            }),
        )
        expect(getToken()).toBe('test-token')
    })

    it('shows the server error on bad credentials', async () => {
        vi.spyOn(sdk, 'login').mockRejectedValue({ detail: 'Incorrect email or password' })
        const { router } = await renderWithRouter(<LoginPage />, { path: '/login' })

        await fillAndSubmit()

        expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password')
        expect(router.state.location.pathname).toBe('/login')
        expect(getToken()).toBeUndefined()
    })

    it('links to registration', async () => {
        await renderWithRouter(<LoginPage />, { path: '/login' })

        expect(screen.getByRole('link', { name: 'Create an account' })).toHaveAttribute(
            'href',
            '/register',
        )
    })
})
