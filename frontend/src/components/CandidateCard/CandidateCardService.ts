import type { Candidate } from '@/api/types.gen'
import { formatMoney } from '@/lib/format'

// Pure helpers for CandidateCard: how a candidate's price and pick button read.

// ----- price -----

/** The candidate's price, e.g. "$1,800.00", or null when the search gave none. */
export function priceLabel(candidate: Candidate): string | null {
    return candidate.price ? formatMoney(candidate.price) : null
}

// ----- pick button -----

/**
 * The pick button's text. A single pick is made at once ("Pick"); a multi pick toggles
 * ("Add" / "Added") and is sent with the list's own button.
 */
export function pickLabel(multi: boolean, selected: boolean): string {
    if (!multi) return 'Pick'
    return selected ? 'Added' : 'Add'
}
