import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
    createMemoryHistory,
    createRootRoute,
    createRoute,
    createRouter,
    Outlet,
    RouterProvider,
} from '@tanstack/react-router'
import { act, render as rtlRender, type RenderOptions } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'

import { colorSchemeManager, cssVariablesResolver, mantineTheme } from '@/lib/mantineTheme'

// Test render helpers. Tests import Testing Library from here, not '@testing-library/react',
// so every render is inside the app's MantineProvider. Components also get a fresh QueryClient
// (no retries, so errors surface immediately) and, when they use router hooks/links, a
// throwaway in-memory router.

// The local `render` below takes precedence over the one in this star export.
export * from '@testing-library/react'

// ----- Mantine -----

function MantineWrapper({ children }: { children: ReactNode }) {
    // env="test" turns off transitions and portals, so overlays render in place, immediately.
    return (
        <MantineProvider
            theme={mantineTheme}
            cssVariablesResolver={cssVariablesResolver}
            colorSchemeManager={colorSchemeManager}
            env="test"
        >
            {children}
        </MantineProvider>
    )
}

/** Testing Library's `render`, wrapped in the app's MantineProvider. */
export function render(ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
    return rtlRender(ui, { wrapper: MantineWrapper, ...options })
}

// ----- query & router -----

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
