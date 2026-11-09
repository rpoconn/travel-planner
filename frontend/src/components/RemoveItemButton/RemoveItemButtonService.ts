import type { ItineraryItem } from '@/lib/itemMeta'

// Pure logic for RemoveItemButton: what the confirm dialog says removing an item does.

// ----- wording -----

/**
 * What removing `item` takes with it and what happens next. A transfer goes alone; any other
 * item takes the rides straight to and from it. Either way the planner then asks how to fix
 * whatever the trip is left without.
 */
export function removalNote(item: ItineraryItem): string {
    const next = 'The planner will then say what the trip needs and ask how to fix it.'
    return item.type === 'transfer'
        ? `Only this ride goes. ${next}`
        : `The rides straight to and from it go too. ${next}`
}
