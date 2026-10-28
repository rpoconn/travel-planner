import type { QueryClient } from '@tanstack/react-query'
import { createRootRouteWithContext, Link, Outlet } from '@tanstack/react-router'

export interface RouterContext {
    queryClient: QueryClient
}

export const Route = createRootRouteWithContext<RouterContext>()({
    component: Outlet,
    notFoundComponent: NotFound,
})

function NotFound() {
    return (
        <main className="flex min-h-screen flex-col items-center justify-center gap-3">
            <h1 className="text-2xl font-semibold">Page not found</h1>
            <Link to="/" className="text-primary text-sm underline-offset-4 hover:underline">
                Back to your trips
            </Link>
        </main>
    )
}
