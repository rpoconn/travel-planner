import type { Gap } from '@/api/types.gen'
import { TransferConnector } from '@/components/TransferConnector/TransferConnector'
import { gapMessage } from '@/lib/timeline'

// Dashed "no transfer planned" connectors, one list item per gap. Renders list items, so it
// must sit directly inside the day's <ol>.

// ----- types -----

interface MissingTransfersProps {
    gaps?: readonly Gap[] | undefined
}

export function MissingTransfers({ gaps = [] }: MissingTransfersProps) {
    // ----- markup -----
    return gaps.map((gap) => (
        <li key={`${gap.after_item_id ?? 'home'}-${gap.before_item_id ?? 'home'}`}>
            <TransferConnector variant="missing" message={gapMessage(gap)} />
        </li>
    ))
}
