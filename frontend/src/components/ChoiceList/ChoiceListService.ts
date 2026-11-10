import type { OfferChoice } from '@/api/types.gen'
import { formatDateRange } from '@/lib/format'

// Pure helpers for ChoiceList: whether a list picks one option at once or several, the
// selection rules for several, and the lines under the prompt.

// ----- mode -----

/** Several options can be picked, so cards toggle and a button sends the selection. */
export function isMulti(action: OfferChoice): boolean {
    return action.max_picks > 1
}

// ----- selection -----

/**
 * `selected` with `id` toggled. Adding past `max` is ignored (the list says how many it
 * takes), so a full selection only shrinks until something is removed.
 */
export function toggleId(selected: readonly string[], id: string, max: number): string[] {
    if (selected.includes(id)) return selected.filter((picked) => picked !== id)
    if (selected.length >= max) return [...selected]
    return [...selected, id]
}

/** The selection is a pick the offer allows. */
export function canSubmit(selected: readonly string[], action: OfferChoice): boolean {
    return selected.length >= action.min_picks && selected.length <= action.max_picks
}

/** "Add 2 picks", or what's still needed: "Pick at least 1". */
export function submitLabel(selected: readonly string[], action: OfferChoice): string {
    const count = selected.length
    if (count < action.min_picks) return `Pick at least ${String(action.min_picks)}`
    return `Add ${String(count)} pick${count === 1 ? '' : 's'}`
}

/** What the list takes, under its prompt: "Pick 1 to 3." Nothing for a single pick. */
export function pickHint(action: OfferChoice): string | null {
    if (!isMulti(action)) return null
    return `Pick ${String(action.min_picks)} to ${String(action.max_picks)}.`
}

const DAY_MS = 24 * 60 * 60 * 1000

/**
 * Which nights a lodging offer is for: "2 nights, May 1 – May 3." The planner asks once per
 * run of nights without a place to stay, so consecutive lists need telling apart. Null for
 * any other offer.
 */
export function nightsHint(action: OfferChoice): string | null {
    if (!action.nights) return null
    const { check_in: checkIn, check_out: checkOut } = action.nights
    // Date-only ISO strings parse as UTC midnight, so the difference is whole days.
    const count = Math.round((Date.parse(checkOut) - Date.parse(checkIn)) / DAY_MS)
    const nights = `${String(count)} night${count === 1 ? '' : 's'}`
    return `${nights}, ${formatDateRange(checkIn, checkOut) ?? ''}.`
}

/**
 * Which gap a transfer offer is for: "From Home to San Francisco International." The planner
 * asks once per gap with the same question, so consecutive lists need telling apart.
 * Home is a gap end with no item next to it. Null for any other offer.
 */
export function gapHint(action: OfferChoice): string | null {
    const { gap } = action
    if (!gap) return null
    const from = gap.after_item_id === null ? 'Home' : gap.from_place.name
    const to = gap.before_item_id === null ? 'Home' : gap.to_place.name
    return `From ${from} to ${to}.`
}
