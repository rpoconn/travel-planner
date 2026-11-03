import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Place, TimelineDay, TimelineWarning } from '@/api/types.gen'
import { emptyDayGaps, zoneLabel, type DayGaps } from '@/lib/timeline'
import {
    activityItem,
    flightItem,
    gap,
    PHILADELPHIA,
    timelineEntry,
    transferItem,
} from '@/test/fixtures'

import { DayColumn } from './DayColumn'

// ----- setup -----

const LANDING = timelineEntry(flightItem(), { continues_from_previous_day: true })
const DINNER = timelineEntry(activityItem({ id: 'dinner', title: 'Dinner in Ginza' }))

const ARRIVAL_DAY: TimelineDay = {
    index: 1,
    date: '2027-05-02',
    entries: [LANDING, timelineEntry(transferItem()), DINNER],
    timezone: 'Asia/Tokyo',
}

interface RenderOptions {
    warnings?: TimelineWarning[]
    previousDay?: TimelineDay
    gaps?: Partial<DayGaps>
    homeAtStart?: Place
    homeAtEnd?: Place
}

function renderDay(
    day: TimelineDay,
    { warnings = [], previousDay, gaps, homeAtStart, homeAtEnd }: RenderOptions = {},
) {
    const onSelectItem = vi.fn()
    render(
        <DayColumn
            day={day}
            previousDay={previousDay}
            gaps={{ ...emptyDayGaps(), ...gaps }}
            homeAtStart={homeAtStart}
            homeAtEnd={homeAtEnd}
            dayWarnings={warnings.filter((warning) => warning.date === day.date)}
            warningsByItem={new Map()}
            onSelectItem={onSelectItem}
        />,
    )
    return { onSelectItem, column: screen.getByRole('region', { name: /^Day 2/ }) }
}

// ----- tests -----

describe('DayColumn', () => {
    it('labels the day and renders cards and transfers in order', () => {
        const { column } = renderDay(ARRIVAL_DAY)

        expect(column).toHaveTextContent('Day 2')
        const items = within(column).getAllByRole('listitem')
        expect(
            items.map((item) =>
                item.querySelector('[data-item-type]')?.getAttribute('data-item-type'),
            ),
        ).toEqual(['flight', 'transfer', 'activity'])
    })

    it('draws a missing transfer above the card it leads to and below the one it leaves', () => {
        const day: TimelineDay = { ...ARRIVAL_DAY, entries: [LANDING, DINNER] }
        const toDinner = gap({
            from_place: { name: 'Narita International Airport', iata: 'NRT' },
            to_place: { name: 'Ginza' },
            after_item_id: 'flight-1',
            before_item_id: 'dinner',
        })
        const home = gap({
            from_place: { name: 'Ginza' },
            to_place: PHILADELPHIA,
            after_item_id: 'dinner',
            before_item_id: null,
        })
        const { column } = renderDay(day, {
            gaps: {
                before: new Map([['dinner', [toDinner]]]),
                after: new Map([['dinner', [home]]]),
            },
            warnings: [
                {
                    kind: 'missing_transfer',
                    message: 'No transfer from Narita International Airport to Ginza',
                    date: '2027-05-02',
                    item_ids: ['flight-1', 'dinner'],
                },
            ],
        })

        const items = within(column).getAllByRole('listitem')
        expect(items).toHaveLength(4)
        expect(items[1]).toHaveTextContent(
            'No transfer planned: No transfer from Narita International Airport to Ginza',
        )
        expect(items[3]).toHaveTextContent('No transfer from Ginza to Home (Philadelphia)')
        // The header counts the day's warnings for screen readers.
        expect(within(column).getByText('1 warning')).toBeInTheDocument()
    })

    it('starts the first day and ends the last at home', () => {
        const day: TimelineDay = { ...ARRIVAL_DAY, entries: [DINNER] }
        const { column } = renderDay(day, {
            homeAtStart: PHILADELPHIA,
            homeAtEnd: PHILADELPHIA,
            gaps: { top: [gap({ to_place: { name: 'Ginza' }, before_item_id: 'lodging-1' })] },
        })

        const items = within(column).getAllByRole('listitem')
        expect(items.map((item) => item.textContent)).toEqual([
            'Home · Philadelphia',
            expect.stringContaining('No transfer planned'),
            expect.stringContaining('Dinner in Ginza'),
            'Home · Philadelphia',
        ])
    })

    it('does not call a day free when a missing transfer is drawn on it', () => {
        const { column } = renderDay(
            { ...ARRIVAL_DAY, entries: [] },
            { gaps: { bottom: [gap({ before_item_id: null, after_item_id: 'lodging-1' })] } },
        )

        expect(column).not.toHaveTextContent('Free day')
        expect(column).toHaveTextContent('No transfer planned')
    })

    it('flags the day the clocks change on landing', () => {
        const departureDay: TimelineDay = {
            index: 0,
            date: '2027-05-01',
            entries: [],
            timezone: 'America/Los_Angeles',
        }
        const { column } = renderDay(ARRIVAL_DAY, { previousDay: departureDay })

        const tokyo = zoneLabel('Asia/Tokyo', new Date(Date.UTC(2027, 4, 2, 12)))
        expect(column).toHaveTextContent(`Clocks +16h · now ${tokyo}`)
    })

    it('shows the zone without a change marker on a day in the same zone', () => {
        const { column } = renderDay(ARRIVAL_DAY, { previousDay: { ...ARRIVAL_DAY, index: 0 } })

        expect(column).toHaveTextContent(
            zoneLabel('Asia/Tokyo', new Date(Date.UTC(2027, 4, 2, 12))),
        )
        expect(column).not.toHaveTextContent('Clocks')
    })

    it('shows a free day when nothing is planned', () => {
        const { column } = renderDay({
            index: 1,
            date: '2027-05-02',
            entries: [],
            timezone: null,
        })

        expect(column).toHaveTextContent('Free day')
    })

    it('opens items from cards and transfer chips', async () => {
        const { onSelectItem } = renderDay(ARRIVAL_DAY)

        await userEvent.click(screen.getByRole('button', { name: /^Narita Express/ }))
        await userEvent.click(screen.getByRole('button', { name: /Dinner in Ginza/ }))

        expect(onSelectItem.mock.calls.map(([item]) => (item as { id: string }).id)).toEqual([
            'transfer-1',
            'dinner',
        ])
    })
})
