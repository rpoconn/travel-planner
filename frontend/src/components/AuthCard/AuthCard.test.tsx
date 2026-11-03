import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AuthCard } from './AuthCard'

describe('AuthCard', () => {
    it('renders the title, description, form content and footer', () => {
        render(
            <AuthCard title="Sign in" description="Welcome back" footer="No account yet?">
                <button type="button">Submit</button>
            </AuthCard>,
        )

        expect(screen.getByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
        expect(screen.getByText('Welcome back')).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument()
        expect(screen.getByText('No account yet?')).toBeInTheDocument()
        expect(screen.getByText('Travel Planner')).toBeInTheDocument()
    })
})
