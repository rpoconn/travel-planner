import { describe, expect, it } from 'vitest'

import { activityItem, flightItem, lodgingItem, transferItem } from '@/test/fixtures'

import { removalNote } from './RemoveItemButtonService'

describe('removalNote', () => {
    it.each([flightItem(), lodgingItem(), activityItem()])(
        'says a $type takes its rides with it',
        (item) => {
            expect(removalNote(item)).toBe(
                'The rides straight to and from it go too. The planner will then say what the ' +
                    'trip needs and ask how to fix it.',
            )
        },
    )

    it('says a ride goes alone', () => {
        expect(removalNote(transferItem())).toBe(
            'Only this ride goes. The planner will then say what the trip needs and ask how to ' +
                'fix it.',
        )
    })
})
