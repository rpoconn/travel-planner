import { describe, expect, it } from 'vitest'

import type { ChangesRecord } from '@/api/types.gen'

import { changesTitle, listsChanges } from './ChangesCardService'

// ----- setup -----

const NONE: ChangesRecord = { type: 'changes', applied: [], rejected: [], clarification: null }
const REMOVED: ChangesRecord = {
    ...NONE,
    applied: [{ name: 'remove_item', summary: 'Removed “Riverside Hotel”', item_id: 'item-1' }],
}
const TURNED_DOWN: ChangesRecord = {
    ...NONE,
    rejected: [{ summary: 'Change “Dinner”: start', reason: 'that is during the flight home' }],
}

// ----- tests -----

describe('listsChanges', () => {
    it('is true for changes made or turned down', () => {
        expect(listsChanges(REMOVED)).toBe(true)
        expect(listsChanges(TURNED_DOWN)).toBe(true)
    })

    it('is false for a record that only asks which item was meant', () => {
        expect(listsChanges({ ...NONE, clarification: 'Which dinner?' })).toBe(false)
    })
})

describe('changesTitle', () => {
    it('says the trip changed only when something was written', () => {
        expect(changesTitle(REMOVED)).toBe('Trip updated')
        expect(changesTitle(TURNED_DOWN)).toBe('Nothing changed')
    })
})
