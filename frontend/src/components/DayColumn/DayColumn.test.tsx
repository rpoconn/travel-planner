import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { TimelineDay, TimelineWarning } from '@/api/types.gen'
import { zoneLabel } from '@/lib/timeline'
import { activityItem, flightItem, timelineEntry, transferItem } from '@/test/fixtures'

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

function renderDay(day: TimelineDay, warnings: TimelineWarning[] = [], previousDay?: TimelineDay) {
    const onSelectItem = vi.fn()
    render(
        <DayColumn
            day={day}
            previousDay={previousDay}
            warnings={warnings}
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

    it('draws a missing-transfer gap between the two flagged cards', () => {
        const day: TimelineDay = {
            ...ARRIVAL_DAY,
            entries: [LANDING, DINNER],
        }
        const { column } = renderDay(day, [
            {
                kind: 'missing_transfer',
                message: 'No transfer from Narita International Airport to Ginza',
                date: '2027-05-02',
                item_ids: ['flight-1', 'dinner'],
            },
        ])

        const items = within(column).getAllByRole('listitem')
        expect(items).toHaveLength(3)
        expect(items[1]).toHaveTextContent('No transfer planned')
        // The header counts the day's warnings for screen readers.
        expect(within(column).getByText('1 warning')).toBeInTheDocument()
    })

    it('flags the day the clocks change on landing', () => {
        const departureDay: TimelineDay = {
            index: 0,
            date: '2027-05-01',
            entries: [],
            timezone: 'America/Los_Angeles',
        }
        const { column } = renderDay(ARRIVAL_DAY, [], departureDay)

        const tokyo = zoneLabel('Asia/Tokyo', new Date(Date.UTC(2027, 4, 2, 12)))
        expect(column).toHaveTextContent(`Clocks +16h · now ${tokyo}`)
    })

    it('shows the zone without a change marker on a day in the same zone', () => {
        const { column } = renderDay(ARRIVAL_DAY, [], { ...ARRIVAL_DAY, index: 0 })

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
