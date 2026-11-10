import { Check, X } from 'lucide-react'

import type { ReviewProposal } from '@/api/types.gen'
import { ProposalChangeRow } from '@/components/ProposalChangeRow/ProposalChangeRow'
import { Button } from '@/components/ui/button'
import type { ProposalDecision } from '@/lib/sse'

// Changes the planner suggests on its own: why, what would change, and Approve / Reject.
// Nothing is written until the user approves. Changes they asked for in their own words
// never come here: those are made at once (ChangesCard shows them).

// ------------ Props ------------

interface ProposalCardProps {
    proposal: ReviewProposal
    /** Disables both buttons, e.g. while a turn streams. */
    disabled?: boolean
    onDecide: (decision: ProposalDecision) => void
}

export function ProposalCard({ proposal, disabled = false, onDecide }: ProposalCardProps) {
    // ------------ Render ------------
    return (
        <section
            aria-label="Suggested changes"
            className="border-primary/40 bg-card flex flex-col gap-2 rounded-xl border px-4 py-3 text-sm"
        >
            <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                Suggested changes
            </h3>
            <p className="leading-snug">{proposal.prompt}</p>
            <ul className="flex flex-col gap-1.5">
                {proposal.changes.map((change, index) => (
                    // Changes have no id; their order is fixed for a given suggestion.
                    <ProposalChangeRow key={`${change.name}-${String(index)}`} change={change} />
                ))}
            </ul>
            <div className="flex items-center gap-2 pt-1">
                <Button
                    size="sm"
                    disabled={disabled}
                    onClick={() => {
                        onDecide('approve')
                    }}
                >
                    <Check aria-hidden />
                    Approve
                </Button>
                <Button
                    size="sm"
                    variant="outline"
                    disabled={disabled}
                    onClick={() => {
                        onDecide('reject')
                    }}
                >
                    <X aria-hidden />
                    Reject
                </Button>
            </div>
        </section>
    )
}
