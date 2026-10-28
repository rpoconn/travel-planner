import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as sdk from '@/api/sdk.gen'

import { App } from './App'

describe('App', () => {
    afterEach(() => {
        vi.restoreAllMocks()
    })

    it('shows online when the health check succeeds', async () => {
        vi.spyOn(sdk, 'getHealth').mockResolvedValue({
            data: { status: 'ok', version: '0.1.0' },
            error: undefined,
            request: new Request('http://test/health'),
            response: new Response(),
        })

        render(<App />)

        expect(await screen.findByText('online')).toBeInTheDocument()
    })

    it('shows offline when the backend is unreachable', async () => {
        vi.spyOn(sdk, 'getHealth').mockRejectedValue(new TypeError('fetch failed'))

        render(<App />)

        expect(await screen.findByText('offline')).toBeInTheDocument()
    })
})
