import { useState } from 'react'

import { ItemDetailBody } from '@/components/ItemDetailBody/ItemDetailBody'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import type { ItineraryItem } from '@/lib/itemMeta'

// Side sheet with every field of one itinerary item. Read-only: changes go through the chat.

// ----- types -----

interface ItemDetailSheetProps {
    /** The item to show; null closes the sheet. */
    item: ItineraryItem | null
    onClose: () => void
}

export function ItemDetailSheet({ item, onClose }: ItemDetailSheetProps) {
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
            </SheetContent>
        </Sheet>
    )
}
