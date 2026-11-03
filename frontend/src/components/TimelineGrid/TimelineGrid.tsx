import type { Timeline } from '@/api/types.gen'
import { DayColumn } from '@/components/DayColumn/DayColumn'
import { LodgingBar } from '@/components/LodgingBar/LodgingBar'
import type { ItineraryItem } from '@/lib/itemMeta'
import {
    dayGridColumn,
    HALF_COLUMNS_PER_DAY,
    layoutLodging,
    placeGaps,
    warningsByDate,
    warningsByItem,
} from '@/lib/timeline'

// The scrolling grid of day columns with the lodging bars laid over their nights.

// ----- types -----

interface TimelineGridProps {
    timeline: Timeline
    onSelectItem: (item: ItineraryItem) => void
}

// ----- constants -----

/** Width of one half-column; a day is two of these. */
const HALF_COLUMN_WIDTH = '7.5rem'
const LODGING_LANE_HEIGHT = '2.25rem'

export function TimelineGrid({ timeline, onSelectItem }: TimelineGridProps) {
    // ----- state -----
    const { placements, laneCount } = layoutLodging(timeline.lodging)
    const byDate = warningsByDate(timeline.warnings)
    const byItem = warningsByItem(timeline.warnings)
    const gapsByDate = placeGaps(timeline)
    const lastIndex = timeline.days.length - 1
    const hasItems = laneCount > 0 || timeline.days.some((day) => day.entries.length > 0)

    // Rows: date headers, one row per lodging lane, then the cards (filling the height).
    const gridTemplateRows = [
        'auto',
        ...Array.from({ length: laneCount }, () => LODGING_LANE_HEIGHT),
        'minmax(0, 1fr)',
    ].join(' ')

    // ----- markup -----
    return (
        <div className="relative min-h-0 flex-1 overflow-auto" data-testid="timeline-scroll">
            <div
                className="grid min-h-full w-max"
                style={{
                    gridTemplateColumns: `repeat(${String(timeline.days.length * HALF_COLUMNS_PER_DAY)}, ${HALF_COLUMN_WIDTH})`,
                    gridTemplateRows,
                }}
            >
                {timeline.days.map((day) => (
                    <DayColumn
                        key={day.date}
                        day={day}
                        previousDay={timeline.days[day.index - 1]}
                        gaps={gapsByDate.get(day.date)}
                        homeAtStart={day.index === 0 ? timeline.home : null}
                        homeAtEnd={day.index === lastIndex ? timeline.home : null}
                        dayWarnings={byDate.get(day.date)}
                        warningsByItem={byItem}
                        style={{ gridColumn: dayGridColumn(day.index), gridRow: '1 / -1' }}
                        onSelectItem={onSelectItem}
                    />
                ))}
                {/* Drawn after the columns so the bars paint above the column borders. */}
                {placements.map(({ span, lane, gridColumnStart, gridColumnEnd }) => (
                    <LodgingBar
                        key={span.item.id}
                        item={span.item}
                        warnings={byItem.get(span.item.id)}
                        style={{
                            gridColumn: `${String(gridColumnStart)} / ${String(gridColumnEnd)}`,
                            gridRow: lane + 2,
                        }}
                        onSelect={onSelectItem}
                    />
                ))}
            </div>
            {!hasItems && (
                <p className="bg-card text-muted-foreground absolute bottom-4 left-4 max-w-xs rounded-xl border px-4 py-3 text-sm shadow-sm">
                    Nothing planned yet. Ask the assistant for flights, a hotel, or ideas for each
                    day.
                </p>
            )}
        </div>
    )
}
