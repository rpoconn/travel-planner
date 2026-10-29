import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'
import { setSessionFromToken } from '@/lib/auth'
import { planSummary, sdkOk, timeline, tokenResponse, travelPlan } from '@/test/fixtures'

import { App } from './App'

// Full app with the real route tree: checks the auth guards end to end (SDK mocked).

// Routes are code-split, so the first render of each page imports its chunk. That can take
// longer than findBy's 1s default when the whole suite runs in parallel.
const ROUTE_LOAD = { timeout: 5000 }

describe('App', () => {
    beforeEach(() => {
        window.history.replaceState(null, '', '/')
    })

    it('sends signed-out visitors to the login page', async () => {
        render(<App />)

        expect(
            await screen.findByRole('heading', { name: 'Welcome back' }, ROUTE_LOAD),
        ).toBeInTheDocument()
        expect(window.location.pathname).toBe('/login')
    })

    it('shows the workspace for a signed-in user, and returns to login on sign out', async () => {
        setSessionFromToken(tokenResponse())
        vi.spyOn(sdk, 'listPlans').mockResolvedValue(sdkOk([]))

        render(<App />)

        expect(await screen.findByText(/No trips yet/, {}, ROUTE_LOAD)).toBeInTheDocument()

        await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

        expect(
            await screen.findByRole('heading', { name: 'Welcome back' }, ROUTE_LOAD),
        ).toBeInTheDocument()
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
        vi.spyOn(sdk, 'listMessages').mockResolvedValue(sdkOk([]))
        vi.spyOn(sdk, 'getTimeline').mockResolvedValue(sdkOk(timeline()))

        render(<App />)
        await userEvent.click(await screen.findByRole('button', { name: 'New trip' }, ROUTE_LOAD))

        await waitFor(() => {
            expect(window.location.pathname).toBe('/plans/new-plan')
        })
        expect(await screen.findByRole('textbox', { name: 'Trip name' })).toHaveValue(
            'Untitled trip',
        )
    })
})
