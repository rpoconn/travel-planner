import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { TypingDots } from './TypingDots'

describe('TypingDots', () => {
    it('announces the assistant is typing with three dots', () => {
        render(<TypingDots />)

        const status = screen.getByRole('status', { name: 'Assistant is typing' })
        expect(status.children).toHaveLength(3)
    })
})
