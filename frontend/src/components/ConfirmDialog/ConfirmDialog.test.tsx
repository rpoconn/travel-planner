import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { render, screen } from '@/test/render'

import { ConfirmDialog } from './ConfirmDialog'

// ----- setup -----

function renderDialog(props: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(
        <ConfirmDialog
            opened
            title="Delete “Tokyo”?"
            confirmLabel="Delete"
            onConfirm={onConfirm}
            onCancel={onCancel}
            {...props}
        >
            This can’t be undone.
        </ConfirmDialog>,
    )
    return { onConfirm, onCancel, user }
}

// ----- tests -----

describe('ConfirmDialog', () => {
    it('asks the question and says what confirming does', () => {
        renderDialog()

        const dialog = screen.getByRole('dialog', { name: 'Delete “Tokyo”?' })
        expect(dialog).toHaveTextContent('This can’t be undone.')
    })

    it('confirms from the confirm button', async () => {
        const { onConfirm, onCancel, user } = renderDialog()

        await user.click(screen.getByRole('button', { name: 'Delete' }))

        expect(onConfirm).toHaveBeenCalledOnce()
        expect(onCancel).not.toHaveBeenCalled()
    })

    it('cancels from the Cancel button and from Escape', async () => {
        const { onConfirm, onCancel, user } = renderDialog()

        await user.click(screen.getByRole('button', { name: 'Cancel' }))
        await user.keyboard('{Escape}')

        expect(onCancel).toHaveBeenCalledTimes(2)
        expect(onConfirm).not.toHaveBeenCalled()
    })

    it("can't be confirmed twice while the action runs", () => {
        renderDialog({ loading: true })

        expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
    })

    it('shows why the action failed', () => {
        renderDialog({ error: 'Server unavailable' })

        expect(screen.getByRole('alert')).toHaveTextContent('Server unavailable')
    })

    it('renders nothing while closed', () => {
        renderDialog({ opened: false })

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
})
