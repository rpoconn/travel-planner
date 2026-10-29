import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { setTheme } from '@/lib/theme'

import { ThemeToggle } from './ThemeToggle'

describe('ThemeToggle', () => {
    afterEach(() => {
        setTheme('light')
    })

    it('switches between light and dark', async () => {
        render(<ThemeToggle />)

        await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))

        expect(document.documentElement).toHaveClass('dark')
        await userEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }))
        expect(document.documentElement).not.toHaveClass('dark')
    })
})
