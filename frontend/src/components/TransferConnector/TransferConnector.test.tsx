import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { transferItem } from '@/test/fixtures'

import { TransferConnector } from './TransferConnector'

describe('TransferConnector', () => {
    it('shows mode, provider and duration, and opens on click', async () => {
        const onSelect = vi.fn()
        render(<TransferConnector variant="transfer" item={transferItem()} onSelect={onSelect} />)

        const chip = screen.getByRole('button', { name: /^Narita Express: Train/ })
        expect(chip).toHaveTextContent('JR East · 1h')
        expect(chip).toHaveAttribute('title', 'Narita International Airport → Tokyo Station')

        await userEvent.click(chip)
        expect(onSelect).toHaveBeenCalledWith(transferItem())
    })

    it('falls back to the mode name without a provider', () => {
        render(
            <TransferConnector
                variant="transfer"
                item={transferItem({ mode: 'taxi', provider: null })}
                onSelect={vi.fn()}
            />,
        )

        expect(screen.getByRole('button')).toHaveTextContent('Taxi · 1h')
    })

    it('labels a chauffeured car as a car service, not a rental', () => {
        render(
            <TransferConnector
                variant="transfer"
                item={transferItem({ mode: 'car_service', provider: null })}
                onSelect={vi.fn()}
            />,
        )

        expect(screen.getByRole('button')).toHaveTextContent('Car service · 1h')
    })

    it('marks a missing transfer', () => {
        render(
            <TransferConnector
                variant="missing"
                message="No transfer from Narita International Airport to Hotel Okura"
            />,
        )

        expect(screen.getByText('No transfer planned')).toBeInTheDocument()
        expect(
            screen.getByText(/No transfer from Narita International Airport to Hotel Okura/),
        ).toBeInTheDocument()
        expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })
})
