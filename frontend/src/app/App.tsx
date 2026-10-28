import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import { createAppRouter } from '@/app/router'
import { getSession, subscribe } from '@/lib/auth'
import { createQueryClient } from '@/lib/queryClient'

export function App() {
    // ----- setup -----
    // Lazy state so StrictMode's double render doesn't create two clients/routers.
    const [queryClient] = useState(createQueryClient)
    const [router] = useState(() => createAppRouter(queryClient))

    // ----- effects -----
    // Cached data belongs to whoever was signed in; drop it the moment the session ends.
    useEffect(
        () =>
            subscribe(() => {
                if (!getSession()) queryClient.clear()
            }),
        [queryClient],
    )

    // ----- markup -----
    return (
        <QueryClientProvider client={queryClient}>
            <RouterProvider router={router} />
        </QueryClientProvider>
    )
}
