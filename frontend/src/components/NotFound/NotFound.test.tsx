import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderWithRouter, screen } from '@/test/render'

import { NotFound } from './NotFound'

describe('NotFound', () => {
    it('links back to the trips list', async () => {
        const { router } = await renderWithRouter(<NotFound />, { path: '/missing' })

        expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
        await userEvent.click(screen.getByRole('link', { name: 'Back to your trips' }))
        expect(router.state.location.pathname).toBe('/')
    })
})
