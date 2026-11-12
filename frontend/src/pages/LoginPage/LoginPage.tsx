import { Button, TextInput } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { type SubmitEvent, useState } from 'react'

import { loginMutation } from '@/api/@tanstack/react-query.gen'
import { AuthCard } from '@/components/AuthCard/AuthCard'
import { errorMessage } from '@/lib/apiError'
import { setSessionFromToken } from '@/lib/auth'

export function LoginPage() {
    // ----- state -----
    const navigate = useNavigate()
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')

    const login = useMutation({
        ...loginMutation(),
        onSuccess: async (response) => {
            setSessionFromToken(response)
            await navigate({ to: '/' })
        },
    })

    // ----- event handlers -----
    function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        login.mutate({ body: { email, password } })
    }

    // ----- markup -----
    return (
        <AuthCard
            title="Welcome back"
            description="Sign in to keep planning your trips."
            footer={
                <>
                    New here?{' '}
                    <Link
                        to="/register"
                        className="text-primary underline-offset-4 hover:underline"
                    >
                        Create an account
                    </Link>
                </>
            }
        >
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <TextInput
                    label="Email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(event) => {
                        setEmail(event.target.value)
                    }}
                />
                <TextInput
                    label="Password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(event) => {
                        setPassword(event.target.value)
                    }}
                />
                {login.isError && (
                    <p role="alert" className="text-destructive text-sm">
                        {errorMessage(login.error)}
                    </p>
                )}
                <Button type="submit" size="sm" disabled={login.isPending}>
                    {login.isPending ? 'Signing in…' : 'Sign in'}
                </Button>
            </form>
        </AuthCard>
    )
}
