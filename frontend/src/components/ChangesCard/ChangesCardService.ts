import type { ChangesRecord } from '@/api/types.gen'

// Pure helpers for ChangesCard: whether a `changes` message lists any change, and how its
// card is titled.

// ----- record -----

/**
 * Whether the record lists changes made or turned down. A record with neither only asks
 * which item was meant (`clarification`), and reads as a plain reply instead.
 */
export function listsChanges(record: ChangesRecord): boolean {
    return record.applied.length > 0 || record.rejected.length > 0
}

/**
 * The card's title. Commands are written all or nothing, so a record with rejections and
 * nothing applied changed nothing.
 */
export function changesTitle(record: ChangesRecord): string {
    return record.applied.length > 0 ? 'Trip updated' : 'Nothing changed'
}
