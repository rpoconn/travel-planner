import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OVERLAP, problem } from '@/test/fixtures'
import { render, screen } from '@/test/render'

import { ProblemRow } from './ProblemRow'

describe('ProblemRow', () => {
    it('asks for a fix of what the trip is missing', async () => {
        const onFix = vi.fn()
        render(<ProblemRow problem={problem()} onFix={onFix} />)

        expect(screen.getByRole('listitem')).toHaveTextContent('A place to stay the night of May 3')
        await userEvent.click(
            screen.getByRole('button', { name: 'Fix this: a place to stay the night of May 3' }),
        )

        expect(onFix).toHaveBeenCalledWith('uncovered_night:2027-05-03')
    })

    it('only lists a warning', () => {
        render(<ProblemRow problem={OVERLAP} onFix={vi.fn()} />)

        expect(screen.getByRole('listitem')).toHaveTextContent('Senso-ji overlaps Tsukiji Market')
        expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })

    it('locks its button while a turn streams', () => {
        render(<ProblemRow problem={problem()} disabled onFix={vi.fn()} />)

        expect(screen.getByRole('button', { name: /^Fix this/ })).toBeDisabled()
    })
})
