import { Trash2 } from 'lucide-react'
import { useState } from 'react'

import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
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
}

export function RemoveItemButton({ item, onRemove, disabled = false }: RemoveItemButtonProps) {
    // ----- state -----
    const [confirming, setConfirming] = useState(false)

    // ----- UI callbacks -----
    function confirm() {
        setConfirming(false)
        onRemove(item)
    }

    // ----- render -----
    return (
        <>
            <Button
                variant="outline"
                className="text-destructive hover:text-destructive w-full"
                disabled={disabled}
                onClick={() => {
                    setConfirming(true)
                }}
            >
                <Trash2 aria-hidden />
                Remove from trip
            </Button>
            <AlertDialog open={confirming} onOpenChange={setConfirming}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove “{item.title}”?</AlertDialogTitle>
                        <AlertDialogDescription>{removalNote(item)}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction variant="destructive" onClick={confirm}>
                            Remove
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    )
}
