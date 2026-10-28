import { useSyncExternalStore } from 'react'

import type { TokenResponse, UserRead } from '@/api/types.gen'

// Auth session store: kept in memory for synchronous reads (API client, route guards) and
// mirrored to localStorage so a reload stays logged in. React reads it via useAuthSession().

// ----- types -----

export interface AuthSession {
    token: string
    user: UserRead
}

// ----- state -----

const STORAGE_KEY = 'travel-planner.auth'

const listeners = new Set<() => void>()
let session: AuthSession | null = readStoredSession()

// ----- storage -----

function isAuthSession(value: unknown): value is AuthSession {
    if (typeof value !== 'object' || value === null) return false
    const candidate = value as Partial<AuthSession>
    return typeof candidate.token === 'string' && typeof candidate.user?.id === 'string'
}

function readStoredSession(): AuthSession | null {
    try {
        const raw = localStorage.getItem(STORAGE_KEY)
        if (raw === null) return null
        const parsed: unknown = JSON.parse(raw)
        return isAuthSession(parsed) ? parsed : null
    } catch {
        // Corrupt JSON or storage disabled (private mode): treat as logged out.
        return null
    }
}

function writeStoredSession(next: AuthSession | null): void {
    try {
        if (next === null) localStorage.removeItem(STORAGE_KEY)
        else localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
        // Storage unavailable: the in-memory session still works for this tab.
    }
}

function emit(): void {
    for (const listener of listeners) listener()
}

// ----- public API -----

export function getSession(): AuthSession | null {
    return session
}

export function getToken(): string | undefined {
    return session?.token
}

export function setSession(next: AuthSession): void {
    session = next
    writeStoredSession(next)
    emit()
}

/** Convenience for the login/register responses. */
export function setSessionFromToken(response: TokenResponse): void {
    setSession({ token: response.access_token, user: response.user })
}

export function clearSession(): void {
    if (session === null) return
    session = null
    writeStoredSession(null)
    emit()
}

export function subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
}

/** Re-read localStorage (used by tests, which reset storage between cases). */
export function reloadSessionFromStorage(): void {
    session = readStoredSession()
    emit()
}

// ----- react -----

export function useAuthSession(): AuthSession | null {
    return useSyncExternalStore(subscribe, getSession)
}
