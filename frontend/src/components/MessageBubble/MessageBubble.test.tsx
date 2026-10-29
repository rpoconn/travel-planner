import { render, screen, within } from '@testing-library/react'
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

    it('shows tool chips above the assistant reply, with their status', () => {
        render(
            <MessageBubble
                role="assistant"
                kind="message"
                content="I added the hotel."
                tools={[
                    { callId: '1', name: 'web_search', status: 'ok', summary: 'Searched the web' },
                    { callId: '2', name: 'add_lodging', status: 'running', summary: 'Adding…' },
                    { callId: '3', name: 'add_flight', status: 'failed', summary: 'Bad dates' },
                ]}
            />,
        )

        const chips = within(screen.getByRole('list', { name: 'Assistant actions' })).getAllByRole(
            'listitem',
        )
        expect(chips.map((chip) => chip.textContent)).toEqual([
            'Searched the web',
            'Adding…',
            'Bad dates',
        ])
        expect(chips[2]?.firstChild).toHaveAttribute('data-status', 'failed')
        expect(screen.getByText('I added the hotel.')).toBeInTheDocument()
    })

    it('shows a staged change as a neutral "proposed" chip', () => {
        render(
            <MessageBubble
                role="assistant"
                kind="message"
                content="Here's my suggestion."
                tools={[
                    {
                        callId: '1',
                        name: 'add_lodging',
                        status: 'proposed',
                        summary: 'Proposed: Add lodging “Hotel Okura”',
                    },
                ]}
            />,
        )

        expect(
            screen.getByText('Proposed: Add lodging “Hotel Okura”').parentElement,
        ).toHaveAttribute('data-status', 'proposed')
    })

    it('renders a decision on a proposal card as a user bubble', () => {
        const { container } = render(
            <MessageBubble role="user" kind="decision" content="Approved the proposed changes." />,
        )

        expect(container.firstChild).toHaveAttribute('data-role', 'user')
    })

    it('shows a typing indicator while waiting for the first token', () => {
        render(<MessageBubble role="assistant" kind="message" content="" streaming />)

        expect(screen.getByRole('status', { name: 'Assistant is typing' })).toBeInTheDocument()
    })

    it('renders an off-topic redirect as a hint card', () => {
        render(
            <MessageBubble
                role="assistant"
                kind="redirect"
                content="Let's get back to your trip!"
            />,
        )

        expect(screen.getByRole('complementary', { name: 'Off-topic reply' })).toHaveTextContent(
            "Let's get back to your trip!",
        )
    })

    it('renders stored errors as a notice', () => {
        const { container } = render(
            <MessageBubble role="system" kind="error" content="The model is unavailable." />,
        )

        expect(container.firstChild).toHaveAttribute('data-role', 'system')
        expect(screen.getByText('The model is unavailable.')).toBeInTheDocument()
    })
})
