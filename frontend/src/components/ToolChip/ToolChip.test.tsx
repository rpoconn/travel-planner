import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ToolChip } from './ToolChip'

describe('ToolChip', () => {
    it('shows the summary and exposes the status', () => {
        const { container } = render(
            <ToolChip
                tool={{ callId: '1', name: 'add_flight', status: 'failed', summary: 'Bad dates' }}
            />,
        )

        expect(screen.getByText('Bad dates')).toHaveAttribute('title', 'Bad dates')
        expect(container.firstChild).toHaveAttribute('data-status', 'failed')
    })

    it('spins while running', () => {
        const { container } = render(
            <ToolChip
                tool={{ callId: '2', name: 'add_lodging', status: 'running', summary: 'Adding…' }}
            />,
        )

        expect(container.querySelector('svg')).toHaveClass('animate-spin')
    })
})
