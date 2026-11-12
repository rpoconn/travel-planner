import { Drawer } from '@mantine/core'
import { useState } from 'react'

import { ItemDetailBody } from '@/components/ItemDetailBody/ItemDetailBody'
import { RemoveItemButton } from '@/components/RemoveItemButton/RemoveItemButton'
import type { ItineraryItem } from '@/lib/itemMeta'

// Side drawer with every field of one itinerary item, and a button to take it off the trip.
// Other changes go through the chat.

// ----- types -----

interface ItemDetailSheetProps {
    /** The item to show; null closes the drawer. */
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
    // Keep showing the last item while the drawer animates closed (item is already null then).
    const [lastItem, setLastItem] = useState(item)
    if (item !== null && item !== lastItem) setLastItem(item)
    const shown = item ?? lastItem

    // While the remove confirm is open on top, Escape is for it alone. Every Mantine overlay
    // listens for Escape on the window, so otherwise one press would close both.
    const [confirming, setConfirming] = useState(false)
    if (item === null && confirming) setConfirming(false)

    // ----- render -----
    return (
        <Drawer.Root
            opened={item !== null}
            onClose={onClose}
            position="right"
            size="md"
            closeOnEscape={!confirming}
        >
            <Drawer.Overlay />
            <Drawer.Content>
                {shown && <ItemDetailBody item={shown} />}
                {shown && onRemove && (
                    <div className="px-4 pb-4">
                        <RemoveItemButton
                            item={shown}
                            onRemove={onRemove}
                            disabled={removeDisabled}
                            onConfirmingChange={setConfirming}
                        />
                    </div>
                )}
                <Drawer.CloseButton />
            </Drawer.Content>
        </Drawer.Root>
    )
}
