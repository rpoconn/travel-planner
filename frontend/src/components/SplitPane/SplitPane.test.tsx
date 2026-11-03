import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { SplitPane } from './SplitPane'

describe('SplitPane', () => {
    beforeEach(() => {
        window.localStorage.clear()
    })

    function setup(storageKey?: string) {
        render(<SplitPane top={<p>Top</p>} bottom={<p>Bottom</p>} storageKey={storageKey} />)
        return screen.getByRole('separator', { name: 'Resize panels' })
    }

    it('renders both panes with the default split', () => {
        const separator = setup()

        expect(screen.getByText('Top')).toBeInTheDocument()
        expect(screen.getByText('Bottom')).toBeInTheDocument()
        expect(separator).toHaveAttribute('aria-valuenow', '40')
    })

    it('moves the divider with the arrow keys and respects the limits', async () => {
        const separator = setup()
        separator.focus()

        await userEvent.keyboard('{ArrowUp}')
        expect(separator).toHaveAttribute('aria-valuenow', '35')

        await userEvent.keyboard('{Home}')
        expect(separator).toHaveAttribute('aria-valuenow', '20')
        await userEvent.keyboard('{ArrowUp}')
        expect(separator).toHaveAttribute('aria-valuenow', '20')

        await userEvent.keyboard('{End}')
        expect(separator).toHaveAttribute('aria-valuenow', '80')
    })

    it('remembers the split between mounts', async () => {
        const separator = setup('split-test')
        separator.focus()
        await userEvent.keyboard('{ArrowDown}')
        expect(window.localStorage.getItem('split-test')).toBe('0.45')

        document.body.innerHTML = ''
        expect(setup('split-test')).toHaveAttribute('aria-valuenow', '45')
    })
})
