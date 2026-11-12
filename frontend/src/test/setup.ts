import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

import { client } from '@/api/client.gen'
import { reloadSessionFromStorage } from '@/lib/auth'

// An absolute base URL: the SDK's SSE client builds a `Request`, which (unlike fetch in a
// browser) can't resolve a relative URL under Node.
client.setConfig({ baseUrl: 'http://api.test' })

// ----- browser APIs jsdom lacks (Mantine needs them) -----

// No media query ever matches, so the colour scheme resolves to light.
Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string): MediaQueryList => ({
        matches: false,
        media: query,
        onchange: null,
        /* eslint-disable @typescript-eslint/no-deprecated -- the MediaQueryList type still requires these legacy methods */
        addListener: () => undefined,
        removeListener: () => undefined,
        /* eslint-enable @typescript-eslint/no-deprecated */
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
    }),
})

class ResizeObserverStub implements ResizeObserver {
    observe(): void {
        // jsdom does no layout, so there is never a resize to report.
    }
    unobserve(): void {
        // Nothing observed, nothing to stop.
    }
    disconnect(): void {
        // Nothing observed, nothing to stop.
    }
}
window.ResizeObserver = ResizeObserverStub

// An autosize Textarea re-measures when web fonts finish loading. (Mantine's published build
// always reports a development env, so its "skip autosize in tests" check never fires.)
// Fonts never load here, so a bare event target is enough.
Object.defineProperty(document, 'fonts', { configurable: true, value: new EventTarget() })

// ----- network -----

// Requests a test didn't mock fail fast instead of reaching the network. Tests override this
// with their own `vi.spyOn(globalThis, 'fetch')` or by spying on SDK functions.
beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Unmocked fetch in a test'))
})

afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    // Each test starts logged out with a fresh store.
    localStorage.clear()
    reloadSessionFromStorage()
})
