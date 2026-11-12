import { Button } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import { useState } from 'react'

import { ConfirmDialog } from '@/components/ConfirmDialog/ConfirmDialog'
import type { ItineraryItem } from '@/lib/itemMeta'

import { removalNote } from './RemoveItemButtonService'

// "Remove from trip" for one itinerary item, behind a confirm dialog. Removing runs a planning
// turn (the parent's `onRemove`): the rules check it, and the planner asks how to fix what the
// trip is left without.

// ----- props -----

interface RemoveItemButtonProps {
    item: ItineraryItem
    /** Told once the removal is confirmed. */
    onRemove: (item: ItineraryItem) => void
    /** While the planner is busy with another turn. */
    disabled?: boolean
    /** Told when the confirm dialog opens and closes (a drawer around it pauses Escape). */
    onConfirmingChange?: (confirming: boolean) => void
}

export function RemoveItemButton({
    item,
    onRemove,
    disabled = false,
    onConfirmingChange,
}: RemoveItemButtonProps) {
    // ----- state -----
    const [confirming, setConfirming] = useState(false)

    // ----- UI callbacks -----
    function setConfirmingAndTell(next: boolean) {
        setConfirming(next)
        onConfirmingChange?.(next)
    }

    function confirm() {
        setConfirmingAndTell(false)
        onRemove(item)
    }

    // ----- render -----
    return (
        <>
            <Button
                size="sm"
                variant="default"
                fullWidth
                className="text-destructive"
                disabled={disabled}
                leftSection={<IconTrash aria-hidden />}
                onClick={() => {
                    setConfirmingAndTell(true)
                }}
            >
                Remove from trip
            </Button>
            <ConfirmDialog
                opened={confirming}
                title={`Remove “${item.title}”?`}
                confirmLabel="Remove"
                onConfirm={confirm}
                onCancel={() => {
                    setConfirmingAndTell(false)
                }}
            >
                {removalNote(item)}
            </ConfirmDialog>
        </>
    )
}
