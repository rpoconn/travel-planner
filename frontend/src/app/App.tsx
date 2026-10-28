import { useEffect, useState } from 'react'

import { getHealth } from '@/api/sdk.gen'

// Placeholder shell for Phase 0: proves the generated SDK can reach the backend.
// Replaced by the router + WorkspacePage in Phase 1.

type ApiStatus = 'checking' | 'online' | 'offline'

export function App() {
    // ----- state -----
    const [status, setStatus] = useState<ApiStatus>('checking')

    // ----- effects -----
    useEffect(() => {
        let cancelled = false
        getHealth()
            .then(({ data }) => {
                if (!cancelled) setStatus(data?.status === 'ok' ? 'online' : 'offline')
            })
            .catch(() => {
                if (!cancelled) setStatus('offline')
            })
        return () => {
            cancelled = true
        }
    }, [])

    // ----- markup -----
    return (
        <main className="bg-background text-foreground flex min-h-screen flex-col items-center justify-center gap-4">
            <h1 className="text-4xl font-semibold tracking-tight">Travel Planner</h1>
            <p className="text-muted-foreground text-sm">
                API status: <span data-testid="api-status">{status}</span>
            </p>
        </main>
    )
}
