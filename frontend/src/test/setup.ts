import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

import { reloadSessionFromStorage } from '@/lib/auth'

afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    // Each test starts logged out with a fresh store.
    localStorage.clear()
    reloadSessionFromStorage()
})
