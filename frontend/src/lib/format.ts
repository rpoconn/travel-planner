// Display formatting shared by the sidebar and workspace.

const MONTH_DAY = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

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
