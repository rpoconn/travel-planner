import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

import { client } from '@/api/client.gen'
import { reloadSessionFromStorage } from '@/lib/auth'

// An absolute base URL: the SDK's SSE client builds a `Request`, which (unlike fetch in a
// browser) can't resolve a relative URL under Node.
client.setConfig({ baseUrl: 'http://api.test' })

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
