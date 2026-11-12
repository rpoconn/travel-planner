import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { render, screen } from '@/test/render'

import { ThemeToggle } from './ThemeToggle'

const STORAGE_KEY = 'travel-planner.theme'

describe('ThemeToggle', () => {
    it('switches between light and dark and remembers the choice', async () => {
        render(<ThemeToggle />)
        const html = document.documentElement

        await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))
        expect(html).toHaveAttribute('data-mantine-color-scheme', 'dark')
        expect(localStorage.getItem(STORAGE_KEY)).toBe('dark')

        await userEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }))
        expect(html).toHaveAttribute('data-mantine-color-scheme', 'light')
        expect(localStorage.getItem(STORAGE_KEY)).toBe('light')
    })

    it('starts from a stored choice', () => {
        localStorage.setItem(STORAGE_KEY, 'dark')

        render(<ThemeToggle />)

        expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeVisible()
    })
})
