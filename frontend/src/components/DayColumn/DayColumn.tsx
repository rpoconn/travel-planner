import { Clock, TriangleAlert } from 'lucide-react'
import { Fragment, type CSSProperties } from 'react'

import type { TimelineDay, TimelineWarning } from '@/api/types.gen'
import { ItemCard } from '@/components/ItemCard/ItemCard'
import { TransferConnector } from '@/components/TransferConnector/TransferConnector'
import type { ItineraryItem } from '@/lib/itemMeta'
import {
    dayZoneChange,
    dayZoneLabel,
    formatDayHeader,
    missingTransferBetween,
} from '@/lib/timeline'

// One day of the timeline. It spans every row of the timeline grid and uses `subgrid`, so
// its sticky date header sits in the first row, its cards in the last, and the lodging lanes
// that ItineraryTimeline draws in between line up with every column. The header shows the
// timezone the traveller is in, and flags the day the clocks change (e.g. landing in Tokyo).

// ----- types -----

interface DayColumnProps {
    day: TimelineDay
    /** The day before, to spot a timezone change; undefined for the first day. */
    previousDay?: TimelineDay
    /** All timeline warnings: used to find `missing_transfer` gaps between cards. */
    warnings: readonly TimelineWarning[]
    /** Warnings dated this day, summarized in the header. */
    dayWarnings?: readonly TimelineWarning[]
    warningsByItem: ReadonlyMap<string, TimelineWarning[]>
    style?: CSSProperties
    onSelectItem: (item: ItineraryItem) => void
}

export function DayColumn({
    day,
    previousDay,
    warnings,
    dayWarnings = [],
    warningsByItem,
    style,
    onSelectItem,
}: DayColumnProps) {
    // ----- state -----
    const { weekday, date } = formatDayHeader(day.date)
    const zone = dayZoneLabel(day)
    const zoneChange = dayZoneChange(day, previousDay)

    // ----- markup -----
    return (
        <section
            aria-label={`Day ${String(day.index + 1)}, ${weekday} ${date}`}
            style={style}
            // last-of-type: lodging bars (buttons) follow the columns in the grid's DOM.
            // min-w-0 + grid-cols-1 (minmax(0, 1fr)): otherwise the column, and its own track,
            // grow to fit their widest content and spill into the next day.
            className="grid min-w-0 grid-cols-1 grid-rows-subgrid border-r last-of-type:border-r-0"
        >
            <header className="bg-background/95 sticky top-0 z-10 flex flex-wrap items-baseline gap-x-2 border-b px-3 py-2 backdrop-blur-sm">
                <span className="text-muted-foreground text-[0.7rem] font-semibold tracking-wider uppercase">
                    Day {day.index + 1}
                </span>
                <span className="font-heading text-base">
                    {weekday}, {date}
                </span>
                {zone && (
                    <span className="text-muted-foreground text-xs" title={day.timezone ?? ''}>
                        {zone}
                    </span>
                )}
                {dayWarnings.length > 0 && (
                    <span
                        className="text-warning ml-auto self-center"
                        title={dayWarnings.map((warning) => warning.message).join('\n')}
                    >
                        <TriangleAlert className="size-4" aria-hidden />
                        <span className="sr-only">
                            {dayWarnings.length} warning{dayWarnings.length === 1 ? '' : 's'}
                        </span>
                    </span>
                )}
                {zoneChange && (
                    <p className="text-flight inline-flex basis-full items-center gap-1 text-xs font-medium">
                        <Clock className="size-3.5" aria-hidden />
                        Clocks {zoneChange.shift} · now {zoneChange.label}
                    </p>
                )}
            </header>

            {/* Last row of the subgrid, below the lodging lanes. */}
            <ol className="flex flex-col gap-0 p-2" style={{ gridRow: '-2 / -1' }}>
                {day.entries.length === 0 && (
                    <li className="text-muted-foreground/70 py-6 text-center text-xs italic">
                        Free day
                    </li>
                )}
                {day.entries.map((entry, index) => {
                    const previous = index > 0 ? day.entries[index - 1] : undefined
                    const gap =
                        previous &&
                        missingTransferBetween(warnings, previous.item.id, entry.item.id)
                    return (
                        <Fragment key={entry.item.id}>
                            {gap && (
                                <li>
                                    <TransferConnector variant="missing" message={gap.message} />
                                </li>
                            )}
                            <li className={entry.item.type === 'transfer' ? '' : 'py-1'}>
                                {entry.item.type === 'transfer' ? (
                                    <TransferConnector
                                        variant="transfer"
                                        item={entry.item}
                                        onSelect={onSelectItem}
                                    />
                                ) : (
                                    <ItemCard
                                        entry={entry}
                                        warnings={warningsByItem.get(entry.item.id)}
                                        onSelect={onSelectItem}
                                    />
                                )}
                            </li>
                        </Fragment>
                    )
                })}
            </ol>
        </section>
    )
}
