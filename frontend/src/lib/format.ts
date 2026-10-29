import type { MoneyOutput, Place } from '@/api/types.gen'

// Display formatting shared by the sidebar, workspace and itinerary.

const MONTH_DAY = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

/** "$1,250.00" / "¥32,000". `amount` is a decimal string (the API serializes Decimal so). */
export function formatMoney({ amount, currency = 'USD' }: MoneyOutput): string {
    const value = Number(amount)
    try {
        return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)
    } catch {
        // Unknown currency code from the model: show it verbatim rather than failing.
        return `${amount} ${currency}`
    }
}

/** Parse an ISO `YYYY-MM-DD` as a *local* calendar date (`new Date(iso)` would use UTC). */
function parseIsoDate(iso: string): Date {
    const [year = 0, month = 1, day = 1] = iso.split('-').map(Number)
    return new Date(year, month - 1, day)
}

/** "May 1 – May 8", "From May 1", "Until May 8", or null when neither date is set. */
export function formatDateRange(start?: string | null, end?: string | null): string | null {
    if (start && end) {
        return `${MONTH_DAY.format(parseIsoDate(start))} – ${MONTH_DAY.format(parseIsoDate(end))}`
    }
    if (start) return `From ${MONTH_DAY.format(parseIsoDate(start))}`
    if (end) return `Until ${MONTH_DAY.format(parseIsoDate(end))}`
    return null
}

/** "Narita International Airport (NRT)". */
export function placeLabel(place: Place): string {
    return place.iata ? `${place.name} (${place.iata})` : place.name
}

/** "Tokyo, Japan", or null when neither is known. */
export function placeLocality(place: Place): string | null {
    return [place.city, place.country].filter(Boolean).join(', ') || null
}
