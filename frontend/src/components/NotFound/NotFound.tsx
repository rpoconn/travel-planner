import { Link } from '@tanstack/react-router'

// Shown by the root route for any URL that doesn't match a route.

export function NotFound() {
    // ----- markup -----
    return (
        <main className="flex min-h-screen flex-col items-center justify-center gap-3">
            <h1 className="text-2xl font-semibold">Page not found</h1>
            <Link to="/" className="text-primary text-sm underline-offset-4 hover:underline">
                Back to your trips
            </Link>
        </main>
    )
}
