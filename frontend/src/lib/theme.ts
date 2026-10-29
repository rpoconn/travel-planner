import { useSyncExternalStore } from 'react'

// Light/dark theme store. The choice is remembered in localStorage; with nothing stored the
// OS preference wins. Applying a theme toggles the `.dark` class on <html>, which is what the
// `dark:` variant and the token overrides in index.css key off.

// ----- types -----

export type Theme = 'light' | 'dark'

// ----- state -----

const STORAGE_KEY = 'travel-planner.theme'

const listeners = new Set<() => void>()
let theme: Theme = readInitialTheme()

// ----- storage -----

function readInitialTheme(): Theme {
    try {
        const stored = localStorage.getItem(STORAGE_KEY)
        if (stored === 'light' || stored === 'dark') return stored
    } catch {
        // Storage disabled: fall through to the OS preference.
    }
    // jsdom (tests) has no matchMedia.
    if (typeof window.matchMedia !== 'function') return 'light'
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(next: Theme): void {
    document.documentElement.classList.toggle('dark', next === 'dark')
}

// ----- public API -----

export function getTheme(): Theme {
    return theme
}

export function setTheme(next: Theme): void {
    theme = next
    applyTheme(next)
    try {
        localStorage.setItem(STORAGE_KEY, next)
    } catch {
        // Storage unavailable: the theme still applies for this tab.
    }
    for (const listener of listeners) listener()
}

export function toggleTheme(): void {
    setTheme(theme === 'dark' ? 'light' : 'dark')
}

/** Apply the stored/OS theme to <html>. Call once at startup, before the first render. */
export function initTheme(): void {
    applyTheme(theme)
}

function subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
}

// ----- react -----

export function useTheme(): Theme {
    return useSyncExternalStore(subscribe, getTheme)
}
