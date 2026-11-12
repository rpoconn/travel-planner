import { describe, expect, it } from 'vitest'

import { gap } from '@/test/fixtures'
import { render, screen } from '@/test/render'

import { MissingTransfers } from './MissingTransfers'

describe('MissingTransfers', () => {
    it('renders one connector per gap', () => {
        render(
            <ol>
                <MissingTransfers
                    gaps={[gap(), gap({ after_item_id: 'a', before_item_id: 'b' })]}
                />
            </ol>,
        )

        expect(screen.getAllByRole('listitem')).toHaveLength(2)
        expect(screen.getAllByText('No transfer planned')).toHaveLength(2)
    })

    it('renders nothing without gaps', () => {
        render(
            <ol>
                <MissingTransfers />
            </ol>,
        )

        expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    })
})
