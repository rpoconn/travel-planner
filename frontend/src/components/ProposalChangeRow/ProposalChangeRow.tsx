import { IconAlertTriangle, IconMinus, IconPencil, IconPlus } from '@tabler/icons-react'

import type { SuggestedChange } from '@/api/types.gen'

import { candidateFacts, candidateWarnings } from './ProposalChangeRowService'

// One change of a suggestion card: what it would do, and for something a search found, the
// facts of what would be added (and how it misses what the user asked for).

// ------------ Props ------------

interface ProposalChangeRowProps {
    change: SuggestedChange
}

export function ProposalChangeRow({ change }: ProposalChangeRowProps) {
    // ------------ Initialization ------------
    const facts = candidateFacts(change)
    const warnings = candidateWarnings(change)
    const Icon =
        change.name === 'add_candidate'
            ? IconPlus
            : change.name === 'remove_item'
              ? IconMinus
              : IconPencil

    // ------------ Render ------------
    return (
        <li className="flex items-start gap-2" data-change={change.name}>
            <Icon className="text-primary mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="flex min-w-0 flex-col">
                <span>{change.summary}</span>
                {facts && <span className="text-muted-foreground text-xs">{facts}</span>}
                {warnings.map((warning) => (
                    <span key={warning} className="text-warning flex items-center gap-1 text-xs">
                        <IconAlertTriangle className="size-3 shrink-0" aria-hidden />
                        {warning}
                    </span>
                ))}
            </span>
        </li>
    )
}
