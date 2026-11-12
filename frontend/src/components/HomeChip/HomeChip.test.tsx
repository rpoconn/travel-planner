import { describe, expect, it } from 'vitest'

import { PHILADELPHIA } from '@/test/fixtures'
import { render, screen } from '@/test/render'

import { HomeChip } from './HomeChip'

describe('HomeChip', () => {
    it('shows the home city', () => {
        render(
            <ol>
                <HomeChip home={PHILADELPHIA} />
            </ol>,
        )

        expect(screen.getByTestId('home-chip')).toHaveTextContent('Home · Philadelphia')
    })

    it('falls back to the place name and puts the address in the title', () => {
        render(
            <ol>
                <HomeChip home={{ name: 'The Loft', address: '1 Main St' }} />
            </ol>,
        )

        const chip = screen.getByTestId('home-chip')
        expect(chip).toHaveTextContent('Home · The Loft')
        expect(chip).toHaveAttribute('title', '1 Main St, The Loft')
    })
})
