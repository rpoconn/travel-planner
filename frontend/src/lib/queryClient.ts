import { QueryClient } from '@tanstack/react-query'

export function createQueryClient(): QueryClient {
    return new QueryClient({
        defaultOptions: {
            queries: {
                staleTime: 30_000,
                // Don't hammer the API on auth/not-found errors; retry once for blips.
                retry: 1,
                refetchOnWindowFocus: false,
            },
        },
    })
}
