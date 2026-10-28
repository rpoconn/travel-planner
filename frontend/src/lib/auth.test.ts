import { describe, expect, it, vi } from 'vitest'

import { ADA, tokenResponse } from '@/test/fixtures'

import {
    clearSession,
    getSession,
    getToken,
    reloadSessionFromStorage,
    setSessionFromToken,
    subscribe,
} from './auth'

const STORAGE_KEY = 'travel-planner.auth'

describe('auth store', () => {
    it('starts logged out', () => {
        expect(getSession()).toBeNull()
        expect(getToken()).toBeUndefined()
    })

    it('stores the session in memory and localStorage and notifies subscribers', () => {
        const listener = vi.fn()
        const unsubscribe = subscribe(listener)

        setSessionFromToken(tokenResponse())

        expect(getToken()).toBe('test-token')
        expect(getSession()?.user).toEqual(ADA)
        expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')).toMatchObject({
            token: 'test-token',
        })
        expect(listener).toHaveBeenCalledTimes(1)
        unsubscribe()
    })

    it('clears the session everywhere', () => {
        setSessionFromToken(tokenResponse())

        clearSession()

        expect(getSession()).toBeNull()
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    })

    it('restores a stored session and ignores corrupt data', () => {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: 'saved', user: ADA }))
        reloadSessionFromStorage()
        expect(getToken()).toBe('saved')

        localStorage.setItem(STORAGE_KEY, '{not json')
        reloadSessionFromStorage()
        expect(getSession()).toBeNull()

        localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: 42 }))
        reloadSessionFromStorage()
        expect(getSession()).toBeNull()
    })
})
