import type { SuggestedChange } from '@/api/types.gen'
import { formatMoney } from '@/lib/format'

// Pure helpers for ProposalChangeRow: what a suggested change shows under its summary.

// ------------ Details ------------

/**
 * The facts of what an `add_candidate` change would add (when, what it costs), on one line:
 * "Sat, May 1 · 10:00 – 12:00 · $40.00". Null for any other change, whose summary says it all.
 */
export function candidateFacts(change: SuggestedChange): string | null {
    const { candidate } = change
    if (!candidate) return null
    const facts = [candidate.subtitle, ...candidate.details]
    if (candidate.price) facts.push(formatMoney(candidate.price))
    const shown = facts.filter((fact) => fact.length > 0)
    return shown.length > 0 ? shown.join(' · ') : null
}

/** How what would be added falls short of what the user asked for; empty for other changes. */
export function candidateWarnings(change: SuggestedChange): string[] {
    return change.candidate?.warnings ?? []
}
