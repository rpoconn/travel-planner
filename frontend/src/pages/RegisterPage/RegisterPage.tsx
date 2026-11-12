import { Button, TextInput } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { type SubmitEvent, useState } from 'react'

import { registerMutation } from '@/api/@tanstack/react-query.gen'
import { AuthCard } from '@/components/AuthCard/AuthCard'
import { errorMessage } from '@/lib/apiError'
import { setSessionFromToken } from '@/lib/auth'

// Mirrors the backend's bcrypt-safe password rule (8–72 bytes).
const MIN_PASSWORD_LENGTH = 8

export function RegisterPage() {
    // ----- state -----
    const navigate = useNavigate()
    const [displayName, setDisplayName] = useState('')
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')

    const register = useMutation({
        ...registerMutation(),
        onSuccess: async (response) => {
            setSessionFromToken(response)
            await navigate({ to: '/' })
        },
    })

    // ----- event handlers -----
    function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        register.mutate({ body: { email, password, display_name: displayName } })
    }

    // ----- markup -----
    return (
        <AuthCard
            title="Create your account"
            description="Save trips and pick up planning where you left off."
            footer={
                <>
                    Already have an account?{' '}
                    <Link to="/login" className="text-primary underline-offset-4 hover:underline">
                        Sign in
                    </Link>
                </>
            }
        >
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <TextInput
                    label="Name (optional)"
                    autoComplete="name"
                    maxLength={100}
                    value={displayName}
                    onChange={(event) => {
                        setDisplayName(event.target.value)
                    }}
                />
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
                    autoComplete="new-password"
                    required
                    minLength={MIN_PASSWORD_LENGTH}
                    description={`At least ${String(MIN_PASSWORD_LENGTH)} characters.`}
                    value={password}
                    onChange={(event) => {
                        setPassword(event.target.value)
                    }}
                />
                {register.isError && (
                    <p role="alert" className="text-destructive text-sm">
                        {errorMessage(register.error)}
                    </p>
                )}
                <Button type="submit" size="sm" disabled={register.isPending}>
                    {register.isPending ? 'Creating account…' : 'Create account'}
                </Button>
            </form>
        </AuthCard>
    )
}
