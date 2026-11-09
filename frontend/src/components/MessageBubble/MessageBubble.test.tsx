import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MessageBubble } from './MessageBubble'

describe('MessageBubble', () => {
    it('renders user text as a right-aligned bubble', () => {
        const { container } = render(
            <MessageBubble role="user" kind="message" content="Find flights to Tokyo" />,
        )

        expect(screen.getByText('Find flights to Tokyo')).toBeInTheDocument()
        expect(container.firstChild).toHaveAttribute('data-role', 'user')
    })

    it('renders a form answer or a pick as a user bubble', () => {
        const { container } = render(<MessageBubble role="user" kind="pick" content="TAP 201" />)

        expect(container.firstChild).toHaveAttribute('data-role', 'user')
    })

    it('renders a planner question as an assistant reply', () => {
        render(
            <MessageBubble role="assistant" kind="expected_input" content="Where are you going?" />,
        )

        expect(screen.getByText('Where are you going?').closest('[data-role]')).toHaveAttribute(
            'data-role',
            'assistant',
        )
    })

    it('shows a typing indicator while waiting for the first token', () => {
        render(<MessageBubble role="assistant" kind="message" content="" streaming />)

        expect(screen.getByRole('status', { name: 'Assistant is typing' })).toBeInTheDocument()
    })

    it('renders a planning nudge as a hint card', () => {
        render(
            <MessageBubble
                role="assistant"
                kind="nudge"
                content="Which dates are you travelling?"
            />,
        )

        expect(
            screen.getByRole('complementary', { name: 'Back to the question' }),
        ).toHaveTextContent('Which dates are you travelling?')
    })

    it('renders a stage move as a divider, not an error', () => {
        render(
            <MessageBubble role="system" kind="stage" content="Basics done. Next up: flights." />,
        )

        expect(
            screen.getByRole('separator', { name: 'Basics done. Next up: flights.' }),
        ).toBeInTheDocument()
    })

    it('renders what a typed edit changed as a list card', () => {
        render(
            <MessageBubble
                role="assistant"
                kind="changes"
                content="Removed “Riverside Hotel”."
                payload={{
                    type: 'changes',
                    applied: [
                        { name: 'remove_item', summary: 'Removed “Riverside Hotel”', item_id: 'a' },
                    ],
                    rejected: [],
                    left_open: [],
                    clarification: null,
                }}
            />,
        )

        expect(screen.getByRole('region', { name: 'Trip updated' })).toHaveTextContent(
            'Removed “Riverside Hotel”',
        )
    })

    it('renders a question asking which item was meant as a plain reply', () => {
        render(
            <MessageBubble
                role="assistant"
                kind="changes"
                content="Which one: the flat or the dinner?"
                payload={{
                    type: 'changes',
                    applied: [],
                    rejected: [],
                    left_open: [],
                    clarification: 'x',
                }}
            />,
        )

        expect(screen.queryByRole('region')).not.toBeInTheDocument()
        expect(
            screen.getByText('Which one: the flat or the dinner?').closest('[data-role]'),
        ).toHaveAttribute('data-role', 'assistant')
    })

    it('renders stored errors as a notice', () => {
        const { container } = render(
            <MessageBubble role="system" kind="error" content="The model is unavailable." />,
        )

        expect(container.firstChild).toHaveAttribute('data-role', 'system')
        expect(screen.getByText('The model is unavailable.')).toBeInTheDocument()
    })
})
