import { createFileRoute, redirect } from '@tanstack/react-router'

import { getSession } from '@/lib/auth'
import { RegisterPage } from '@/pages/RegisterPage/RegisterPage'

export const Route = createFileRoute('/register')({
    beforeLoad: () => {
        if (getSession()) throw redirect({ to: '/' })
    },
    component: RegisterPage,
})
