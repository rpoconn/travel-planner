import { createFileRoute, redirect } from '@tanstack/react-router'

import { getSession } from '@/lib/auth'
import { LoginPage } from '@/pages/LoginPage/LoginPage'

export const Route = createFileRoute('/login')({
    beforeLoad: () => {
        if (getSession()) throw redirect({ to: '/' })
    },
    component: LoginPage,
})
