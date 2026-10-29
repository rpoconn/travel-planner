import { TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'

import type { TransferItem } from '@/api/types.gen'
import { TRANSFER_MODE_META } from '@/lib/itemMeta'
import { formatDuration, formatWallTime } from '@/lib/timeline'
import { cn } from '@/lib/utils'

// The link between two stops in a day column: a transfer item drawn as a small chip on a
// vertical connector line, or a dashed "no transfer planned" chip where the server flagged a
// `missing_transfer` gap between two consecutive cards.

// ----- types -----

type TransferConnectorProps =
    | { variant: 'transfer'; item: TransferItem; onSelect: (item: TransferItem) => void }
    | { variant: 'missing'; message: string }

export function TransferConnector(props: TransferConnectorProps) {
    // ----- markup -----
    if (props.variant === 'missing') {
        return (
            <Connector dashed>
                <span
                    className="border-warning/50 bg-warning/8 text-foreground inline-flex items-center gap-1.5 rounded-full border border-dashed px-2.5 py-1 text-xs"
                    title={props.message}
                >
                    <TriangleAlert className="text-warning size-3.5" aria-hidden />
                    No transfer planned
                    <span className="sr-only">: {props.message}</span>
                </span>
            </Connector>
        )
    }

    const { item, onSelect } = props
    const mode = TRANSFER_MODE_META[item.mode]
    const Icon = mode.icon
    const label = [item.provider ?? mode.label, formatDuration(item.start, item.end)].join(' · ')

    return (
        <Connector>
            <button
                type="button"
                data-item-type="transfer"
                aria-label={`${item.title}: ${mode.label}, ${formatWallTime(item.start)}`}
                title={`${item.from_place.name} → ${item.to_place.name}`}
                className={cn(
                    'bg-transfer/12 hover:bg-transfer/20 inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors',
                    item.status === 'proposed' && 'border-dashed',
                    item.status === 'cancelled' && 'line-through opacity-60',
                )}
                onClick={() => {
                    onSelect(item)
                }}
            >
                <Icon className="text-transfer size-3.5 shrink-0" aria-hidden />
                <span className="text-muted-foreground shrink-0 whitespace-nowrap">
                    {formatWallTime(item.start)}
                </span>
                <span className="truncate">{label}</span>
            </button>
        </Connector>
    )
}

// ----- subcomponents -----

/** Centers its chip on a short vertical line joining the cards above and below. */
function Connector({ children, dashed = false }: { children: ReactNode; dashed?: boolean }) {
    return (
        <div className="relative flex justify-center py-1">
            <span
                aria-hidden
                className={cn(
                    'border-border absolute inset-y-0 left-1/2 border-l-2',
                    dashed && 'border-dashed',
                )}
            />
            {/* Opaque backing so the line doesn't show through the translucent chip. */}
            <div className="bg-card relative max-w-full rounded-full">{children}</div>
        </div>
    )
}
