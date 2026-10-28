import { createFileRoute, Navigate, Outlet, redirect } from '@tanstack/react-router'

import { getSession, useAuthSession } from '@/lib/auth'

// Pathless layout guarding every signed-in route.
export const Route = createFileRoute('/_authed')({
    // Guard on navigation...
    beforeLoad: () => {
        if (!getSession()) throw redirect({ to: '/login' })
    },
    component: AuthedLayout,
})

function AuthedLayout() {
    // ...and reactively, for sessions that end while a page is open (logout, expired token).
    const session = useAuthSession()
    if (!session) return <Navigate to="/login" />
    return <Outlet />
}
