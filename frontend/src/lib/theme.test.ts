import { afterEach, describe, expect, it } from 'vitest'

import { getTheme, initTheme, setTheme, toggleTheme } from './theme'

const STORAGE_KEY = 'travel-planner.theme'

describe('theme store', () => {
    afterEach(() => {
        setTheme('light')
    })

    it('defaults to light when nothing is stored and matchMedia is unavailable', () => {
        initTheme()
        expect(getTheme()).toBe('light')
        expect(document.documentElement).not.toHaveClass('dark')
    })

    it('toggles the dark class on <html> and remembers the choice', () => {
        toggleTheme()

        expect(getTheme()).toBe('dark')
        expect(document.documentElement).toHaveClass('dark')
        expect(localStorage.getItem(STORAGE_KEY)).toBe('dark')

        toggleTheme()
        expect(document.documentElement).not.toHaveClass('dark')
        expect(localStorage.getItem(STORAGE_KEY)).toBe('light')
    })
})
