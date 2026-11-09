import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { lodgingItem } from '@/test/fixtures'

import { RemoveItemButton } from './RemoveItemButton'

describe('RemoveItemButton', () => {
    it('asks first, then removes the item', async () => {
        const user = userEvent.setup()
        const onRemove = vi.fn()
        const item = lodgingItem()
        render(<RemoveItemButton item={item} onRemove={onRemove} />)

        await user.click(screen.getByRole('button', { name: 'Remove from trip' }))

        const dialog = screen.getByRole('alertdialog', { name: 'Remove “Hotel Okura”?' })
        expect(dialog).toHaveTextContent('The rides straight to and from it go too.')
        expect(onRemove).not.toHaveBeenCalled()

        await user.click(screen.getByRole('button', { name: 'Remove' }))

        expect(onRemove).toHaveBeenCalledWith(item)
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    })

    it('keeps the item when cancelled', async () => {
        const user = userEvent.setup()
        const onRemove = vi.fn()
        render(<RemoveItemButton item={lodgingItem()} onRemove={onRemove} />)

        await user.click(screen.getByRole('button', { name: 'Remove from trip' }))
        await user.click(screen.getByRole('button', { name: 'Cancel' }))

        expect(onRemove).not.toHaveBeenCalled()
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    })

    it('is disabled while the planner is busy', () => {
        render(<RemoveItemButton item={lodgingItem()} onRemove={vi.fn()} disabled />)

        expect(screen.getByRole('button', { name: 'Remove from trip' })).toBeDisabled()
    })
})
