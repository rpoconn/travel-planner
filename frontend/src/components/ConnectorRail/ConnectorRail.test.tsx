import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ConnectorRail } from './ConnectorRail'

describe('ConnectorRail', () => {
    it('renders its chip on a solid line by default', () => {
        const { container } = render(<ConnectorRail>chip</ConnectorRail>)

        expect(screen.getByText('chip')).toBeInTheDocument()
        expect(container.querySelector('span[aria-hidden]')).not.toHaveClass('border-dashed')
    })

    it('draws a dashed line when asked', () => {
        const { container } = render(<ConnectorRail dashed>chip</ConnectorRail>)

        expect(container.querySelector('span[aria-hidden]')).toHaveClass('border-dashed')
    })
})
