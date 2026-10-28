import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
    createMemoryHistory,
    createRootRoute,
    createRoute,
    createRouter,
    Outlet,
    RouterProvider,
} from '@tanstack/react-router'
import { act, render } from '@testing-library/react'
import type { ReactElement } from 'react'

// Test render helpers. Components get a fresh QueryClient (no retries, so errors surface
// immediately) and, when they use router hooks/links, a throwaway in-memory router.

export function createTestQueryClient(): QueryClient {
    return new QueryClient({
        defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
        },
    })
}

export function renderWithQuery(ui: ReactElement) {
    const queryClient = createTestQueryClient()
    const result = render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
    return { ...result, queryClient }
}

/**
 * Render `ui` at `path` inside a minimal router. Any navigation away lands on a stub page, so
 * tests assert on `router.state.location.pathname` instead of rendering the real target.
 */
export async function renderWithRouter(ui: ReactElement, { path = '/' } = {}) {
    const queryClient = createTestQueryClient()

    const rootRoute = createRootRoute({ component: Outlet })
    const pageRoute = createRoute({ getParentRoute: () => rootRoute, path, component: () => ui })
    const stub = () => <p>navigated away</p>
    const otherRoutes = [
        createRoute({ getParentRoute: () => rootRoute, path: '$', component: stub }),
        ...(path === '/'
            ? []
            : [createRoute({ getParentRoute: () => rootRoute, path: '/', component: stub })]),
    ]
    const router = createRouter({
        routeTree: rootRoute.addChildren([pageRoute, ...otherRoutes]),
        history: createMemoryHistory({ initialEntries: [path] }),
    })

    await act(() => router.load())
    const result = render(
        <QueryClientProvider client={queryClient}>
            <RouterProvider router={router} />
        </QueryClientProvider>,
    )
    return { ...result, router, queryClient }
}
