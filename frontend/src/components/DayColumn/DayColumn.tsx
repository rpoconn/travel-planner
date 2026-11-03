import { Clock, House, TriangleAlert } from 'lucide-react'
import { Fragment, type CSSProperties } from 'react'

import type { Gap, Place, TimelineDay, TimelineWarning } from '@/api/types.gen'
import { ItemCard } from '@/components/ItemCard/ItemCard'
import { TransferConnector } from '@/components/TransferConnector/TransferConnector'
import type { ItineraryItem } from '@/lib/itemMeta'
import {
    dayZoneChange,
    dayZoneLabel,
    emptyDayGaps,
    formatDayHeader,
    gapMessage,
    type DayGaps,
} from '@/lib/timeline'

// One day of the timeline. It spans every row of the timeline grid and uses `subgrid`, so
// its sticky date header sits in the first row, its cards in the last, and the lodging lanes
// that ItineraryTimeline draws in between line up with every column. The header shows the
// timezone the traveller is in, and flags the day the clocks change (e.g. landing in Tokyo).
// Missing transfers (gaps) are drawn as dashed connectors where ItineraryTimeline placed
// them, and the first and last days start / end at a "Home" chip when the trip has one.

// ----- types -----

interface DayColumnProps {
    day: TimelineDay
    /** The day before, to spot a timezone change; undefined for the first day. */
    previousDay?: TimelineDay
    /** Missing transfers drawn on this day (see `placeGaps`). */
    gaps?: DayGaps | undefined
    /** Home, on the first day: drawn above everything else. */
    homeAtStart?: Place | null
    /** Home, on the last day: drawn below everything else. */
    homeAtEnd?: Place | null
    /** Warnings dated this day, summarized in the header. */
    dayWarnings?: readonly TimelineWarning[]
    warningsByItem: ReadonlyMap<string, TimelineWarning[]>
    style?: CSSProperties
    onSelectItem: (item: ItineraryItem) => void
}

const EMPTY_GAPS: DayGaps = emptyDayGaps()

export function DayColumn({
    day,
    previousDay,
    gaps = EMPTY_GAPS,
    homeAtStart = null,
    homeAtEnd = null,
    dayWarnings = [],
    warningsByItem,
    style,
    onSelectItem,
}: DayColumnProps) {
    // ----- state -----
    const { weekday, date } = formatDayHeader(day.date)
    const zone = dayZoneLabel(day)
    const zoneChange = dayZoneChange(day, previousDay)
    const freeDay = day.entries.length === 0 && gaps.top.length === 0 && gaps.bottom.length === 0

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
                {homeAtStart && <HomeChip home={homeAtStart} />}
                <MissingTransfers gaps={gaps.top} />
                {freeDay && (
                    <li className="text-muted-foreground/70 py-6 text-center text-xs italic">
                        Free day
                    </li>
                )}
                {day.entries.map((entry) => (
                    <Fragment key={entry.item.id}>
                        <MissingTransfers gaps={gaps.before.get(entry.item.id)} />
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
                        <MissingTransfers gaps={gaps.after.get(entry.item.id)} />
                    </Fragment>
                ))}
                <MissingTransfers gaps={gaps.bottom} />
                {homeAtEnd && <HomeChip home={homeAtEnd} />}
            </ol>
        </section>
    )
}

// ----- subcomponents -----

function MissingTransfers({ gaps = [] }: { gaps?: readonly Gap[] | undefined }) {
    return gaps.map((gap) => (
        <li key={`${gap.after_item_id ?? 'home'}-${gap.before_item_id ?? 'home'}`}>
            <TransferConnector variant="missing" message={gapMessage(gap)} />
        </li>
    ))
}

/** Where the trip leaves from and comes back to. */
function HomeChip({ home }: { home: Place }) {
    return (
        <li className="flex justify-center py-1">
            <span
                data-testid="home-chip"
                className="bg-secondary text-secondary-foreground inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                title={[home.address, home.city ?? home.name].filter(Boolean).join(', ')}
            >
                <House className="size-3.5 shrink-0" aria-hidden />
                <span className="truncate">Home · {home.city ?? home.name}</span>
            </span>
        </li>
    )
}
