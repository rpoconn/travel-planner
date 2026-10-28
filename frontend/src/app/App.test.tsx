import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { setSessionFromToken } from '@/lib/auth'
import { planSummary, sdkOk, tokenResponse, travelPlan } from '@/test/fixtures'

import { App } from './App'

// Full app with the real route tree: checks the auth guards end to end (SDK mocked).

describe('App', () => {
    beforeEach(() => {
        window.history.replaceState(null, '', '/')
    })

    it('sends signed-out visitors to the login page', async () => {
        render(<App />)

        expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
        expect(window.location.pathname).toBe('/login')
    })

    it('shows the workspace for a signed-in user, and returns to login on sign out', async () => {
        setSessionFromToken(tokenResponse())
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([]))

        render(<App />)

        expect(await screen.findByText(/No trips yet/)).toBeInTheDocument()

        await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

        expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
        await waitFor(() => {
            expect(window.location.pathname).toBe('/login')
        })
    })

    it('keeps the sidebar mounted when a new trip navigates to its page', async () => {
        // Regression: `/` and `/plans/$planId` once rendered separate WorkspacePage instances,
        // so the rename box opened after "New trip" vanished on navigation.
        setSessionFromToken(tokenResponse())
        const created = planSummary({ id: 'new-plan', title: 'Untitled trip' })
        vi.spyOn(sdk, 'listPlans')
            .mockResolvedValueOnce(sdkOk([]))
            .mockResolvedValue(sdkOk([created]))
        vi.spyOn(sdk, 'createPlan').mockResolvedValue(
            sdkOk(travelPlan({ id: 'new-plan', title: 'Untitled trip' })),
        )
        vi.spyOn(sdk, 'getPlan').mockResolvedValue(
            sdkOk(travelPlan({ id: 'new-plan', title: 'Untitled trip' })),
        )

        render(<App />)
        await userEvent.click(await screen.findByRole('button', { name: 'New trip' }))

        await waitFor(() => {
            expect(window.location.pathname).toBe('/plans/new-plan')
        })
        expect(await screen.findByRole('textbox', { name: 'Trip name' })).toHaveValue(
            'Untitled trip',
        )
    })
})
