import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { STAGE_META } from '@/lib/stages'

import { EmptyChat } from './EmptyChat'

describe('EmptyChat', () => {
    it('shows the stage prompt and the basics intro', () => {
        render(<EmptyChat stage="basics" suggestions={[]} onPick={vi.fn()} />)

        expect(screen.getByText(STAGE_META.basics.prompt)).toBeInTheDocument()
        expect(screen.getByText(/Tell me where and when/)).toBeInTheDocument()
    })

    it('reports the clicked suggestion', async () => {
        const onPick = vi.fn()
        render(
            <EmptyChat stage="basics" suggestions={['Plan Tokyo', 'Plan Rome']} onPick={onPick} />,
        )

        await userEvent.click(screen.getByRole('button', { name: 'Plan Rome' }))

        expect(onPick).toHaveBeenCalledWith('Plan Rome')
    })
})
