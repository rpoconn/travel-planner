import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { startTurn, type ChatTurn } from '@/lib/chatTurn'

import { LiveTurn } from './LiveTurn'

// ----- setup -----

function turnWith(overrides: Partial<ChatTurn> = {}): ChatTurn {
    return { ...startTurn('Find flights'), ...overrides }
}

// ----- tests -----

describe('LiveTurn', () => {
    it('shows the user message and a typing indicator while waiting', () => {
        render(<LiveTurn turn={turnWith()} />)

        expect(screen.getByText('Find flights')).toBeInTheDocument()
        expect(screen.getByRole('status', { name: 'Assistant is typing' })).toBeInTheDocument()
    })

    it('shows the streamed reply text', () => {
        render(<LiveTurn turn={turnWith({ text: 'Looking now' })} />)

        expect(screen.getByText('Looking now')).toBeInTheDocument()
    })

    it('renders the off-topic reply as a hint card', () => {
        render(<LiveTurn turn={turnWith({ onTopic: false, text: 'Back to travel?' })} />)

        expect(screen.getByLabelText('Off-topic reply')).toHaveTextContent('Back to travel?')
    })

    it('shows an error notice when the turn failed', () => {
        render(<LiveTurn turn={turnWith({ status: 'error', error: 'Boom' })} />)

        expect(screen.getByText('Boom')).toBeInTheDocument()
    })
})
