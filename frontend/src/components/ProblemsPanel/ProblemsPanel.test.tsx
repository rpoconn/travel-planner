import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OVERLAP, problem, travelPlan } from '@/test/fixtures'
import { render, screen, within } from '@/test/render'

import { ProblemsPanel } from './ProblemsPanel'

const NO_FLIGHT = problem({
    id: 'missing_return',
    code: 'missing_return',
    message: 'a flight home',
})

const panel = () => screen.getByRole('region', { name: 'What the trip still needs' })

describe('ProblemsPanel', () => {
    it('counts and lists what the trip is missing, then the warnings', () => {
        const plan = travelPlan({ problems: [OVERLAP, NO_FLIGHT, problem()] })
        render(<ProblemsPanel plan={plan} onFix={vi.fn()} />)

        expect(panel()).toHaveTextContent('2 things left')
        const rows = within(panel()).getAllByRole('listitem')
        expect(rows.map((row) => row.textContent)).toEqual([
            'A flight homeFix this',
            'A place to stay the night of May 3Fix this',
            'Senso-ji overlaps Tsukiji Market',
        ])
    })

    it('asks the planner to work on the problem picked', async () => {
        const onFix = vi.fn()
        render(<ProblemsPanel plan={travelPlan({ problems: [NO_FLIGHT] })} onFix={onFix} />)

        await userEvent.click(screen.getByRole('button', { name: 'Fix this: a flight home' }))

        expect(onFix).toHaveBeenCalledWith('missing_return')
    })

    it('locks the buttons while a turn streams', () => {
        render(
            <ProblemsPanel plan={travelPlan({ problems: [NO_FLIGHT] })} disabled onFix={vi.fn()} />,
        )

        expect(screen.getByRole('button', { name: 'Fix this: a flight home' })).toBeDisabled()
    })

    it('says so when the trip is complete, and still lists its warnings', () => {
        render(
            <ProblemsPanel
                plan={travelPlan({ complete: true, problems: [OVERLAP] })}
                onFix={vi.fn()}
            />,
        )

        expect(panel()).toHaveTextContent('Trip complete')
        expect(panel()).not.toHaveTextContent('things left')
        expect(within(panel()).getByRole('listitem')).toHaveTextContent(
            'Senso-ji overlaps Tsukiji Market',
        )
    })

    it('has no list when nothing is wrong', () => {
        render(<ProblemsPanel plan={travelPlan({ complete: true })} onFix={vi.fn()} />)

        expect(within(panel()).queryByRole('list')).not.toBeInTheDocument()
    })
})
