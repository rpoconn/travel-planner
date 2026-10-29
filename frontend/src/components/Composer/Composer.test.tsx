import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { Composer, MAX_MESSAGE_CHARS } from './Composer'

// ----- setup -----

/** Composer is controlled; this wrapper owns the text like ChatPanel does. */
function Harness({ busy = false, onSubmit }: { busy?: boolean; onSubmit: (v: string) => void }) {
    const [value, setValue] = useState('')
    return <Composer value={value} onChange={setValue} onSubmit={onSubmit} busy={busy} />
}

function renderComposer(busy = false) {
    const onSubmit = vi.fn()
    render(<Harness busy={busy} onSubmit={onSubmit} />)
    return {
        onSubmit,
        user: userEvent.setup(),
        box: screen.getByRole('textbox', { name: 'Message' }),
    }
}

// ----- tests -----

describe('Composer', () => {
    it('sends on Enter and adds a newline on Shift+Enter', async () => {
        const { onSubmit, user, box } = renderComposer()

        await user.type(box, 'Line one{Shift>}{Enter}{/Shift}Line two')
        expect(box).toHaveValue('Line one\nLine two')
        expect(onSubmit).not.toHaveBeenCalled()

        await user.keyboard('{Enter}')
        expect(onSubmit).toHaveBeenCalledWith('Line one\nLine two')
    })

    it('sends with the button', async () => {
        const { onSubmit, user, box } = renderComposer()

        await user.type(box, 'Hotels in Kyoto')
        await user.click(screen.getByRole('button', { name: 'Send message' }))

        expect(onSubmit).toHaveBeenCalledWith('Hotels in Kyoto')
    })

    it('does not send blank text', async () => {
        const { onSubmit, user, box } = renderComposer()

        await user.type(box, '   {Enter}')

        expect(onSubmit).not.toHaveBeenCalled()
        expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
    })

    it('allows typing but not sending while busy', async () => {
        const { onSubmit, user, box } = renderComposer(true)

        await user.type(box, 'next question{Enter}')

        expect(box).toHaveValue('next question')
        expect(onSubmit).not.toHaveBeenCalled()
        expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
    })

    it('caps length at the backend limit and shows a counter near it', () => {
        const onChange = vi.fn()
        render(<Composer value={'x'.repeat(3900)} onChange={onChange} onSubmit={vi.fn()} />)

        expect(screen.getByRole('textbox', { name: 'Message' })).toHaveAttribute(
            'maxLength',
            String(MAX_MESSAGE_CHARS),
        )
        expect(screen.getByText(`3900 / ${String(MAX_MESSAGE_CHARS)}`)).toBeInTheDocument()
    })
})
