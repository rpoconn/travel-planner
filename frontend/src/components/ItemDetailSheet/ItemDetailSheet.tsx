import { useState } from 'react'

import { ItemDetailBody } from '@/components/ItemDetailBody/ItemDetailBody'
import { RemoveItemButton } from '@/components/RemoveItemButton/RemoveItemButton'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import type { ItineraryItem } from '@/lib/itemMeta'

// Side sheet with every field of one itinerary item, and a button to take it off the trip.
// Other changes go through the chat.

// ----- types -----

interface ItemDetailSheetProps {
    /** The item to show; null closes the sheet. */
    item: ItineraryItem | null
    onClose: () => void
    /** Takes the item off the trip (a planning turn); no Remove button without it. */
    onRemove?: (item: ItineraryItem) => void
    /** While the planner is busy with another turn. */
    removeDisabled?: boolean
}

export function ItemDetailSheet({
    item,
    onClose,
    onRemove,
    removeDisabled = false,
}: ItemDetailSheetProps) {
    // ----- state -----
    // Keep showing the last item while the sheet animates closed (item is already null then).
    const [lastItem, setLastItem] = useState(item)
    if (item !== null && item !== lastItem) setLastItem(item)
    const shown = item ?? lastItem

    // ----- markup -----
    return (
        <Sheet
            open={item !== null}
            onOpenChange={(open) => {
                if (!open) onClose()
            }}
        >
            <SheetContent className="w-full overflow-y-auto sm:max-w-md">
                {shown && <ItemDetailBody item={shown} />}
                {shown && onRemove && (
                    <div className="px-4 pb-4">
                        <RemoveItemButton
                            item={shown}
                            onRemove={onRemove}
                            disabled={removeDisabled}
                        />
                    </div>
                )}
            </SheetContent>
        </Sheet>
    )
}
