import { useMutation } from '@tanstack/react-query'
import { House } from 'lucide-react'
import { type SubmitEvent, useId, useState } from 'react'

import { updateMeMutation } from '@/api/@tanstack/react-query.gen'
import type { Place } from '@/api/types.gen'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetFooter,
    SheetHeader,
    SheetTitle,
} from '@/components/ui/sheet'
import { errorMessage } from '@/lib/apiError'
import { setSession, useAuthSession } from '@/lib/auth'

// The header's home button and the sheet that edits it. Home is where new trips start and
// end: the server copies it into a new plan's origin, the itinerary draws "Home" at both
// ends, and its nearest airport (IATA) is where flight searches depart. A user without a
// home gets the sheet opened for them once, non-modal, so the app stays usable behind it.

// ----- helpers -----

const IATA = /^[A-Z]{3}$/

/** The form's fields, as typed. */
interface HomeDraft {
    city: string
    address: string
    iata: string
}

function draftFrom(home: Place | null | undefined): HomeDraft {
    return {
        city: home?.city ?? home?.name ?? '',
        address: home?.address ?? '',
        iata: home?.iata ?? '',
    }
}

function placeFrom(draft: HomeDraft): Place {
    const city = draft.city.trim()
    return {
        name: city,
        city,
        address: draft.address.trim() || null,
        iata: draft.iata.trim().toUpperCase() || null,
    }
}

/** "Philadelphia" for the header button. */
function homeName(home: Place): string {
    return home.city ?? home.name
}

export function HomeSetup() {
    // ----- state -----
    const session = useAuthSession()
    const home = session?.user.home ?? null
    const fieldId = useId()

    // Opened automatically (and non-modal) only on first mount without a home; once closed
    // it stays closed until the user clicks the button.
    const [open, setOpen] = useState(home === null)
    const [modal, setModal] = useState(home !== null)
    const [draft, setDraft] = useState<HomeDraft>(() => draftFrom(home))

    const updateMe = useMutation({
        ...updateMeMutation(),
        onSuccess: (user) => {
            // The session is the source of truth for the signed-in user (and survives reloads).
            const current = session
            if (current) setSession({ ...current, user })
            setOpen(false)
        },
    })

    const iata = draft.iata.trim().toUpperCase()
    const iataInvalid = iata !== '' && !IATA.test(iata)

    // ----- event handlers -----
    function openSheet() {
        setDraft(draftFrom(home))
        updateMe.reset()
        setModal(true)
        setOpen(true)
    }

    function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (iataInvalid || draft.city.trim() === '') return
        updateMe.mutate({ body: { home: placeFrom(draft) } })
    }

    function setField(field: keyof HomeDraft, value: string) {
        setDraft((previous) => ({ ...previous, [field]: value }))
    }

    // ----- markup -----
    if (!session) return null

    return (
        <>
            <Button size="sm" variant="ghost" onClick={openSheet}>
                <House aria-hidden />
                {home ? `Home: ${homeName(home)}` : 'Set home'}
            </Button>

            <Sheet open={open} onOpenChange={setOpen} modal={modal}>
                <SheetContent
                    // Non-modal: clicking the app behind the sheet must not dismiss it.
                    onInteractOutside={(event) => {
                        if (!modal) event.preventDefault()
                    }}
                >
                    <form onSubmit={handleSubmit} className="flex h-full flex-col">
                        <SheetHeader>
                            <SheetTitle>Where is home?</SheetTitle>
                            <SheetDescription>
                                New trips start and end here, and the planner makes sure you have a
                                way to get from home to your first stop and back. You can still
                                change where one trip starts in its chat.
                            </SheetDescription>
                        </SheetHeader>

                        <div className="flex flex-col gap-4 px-4">
                            <div className="grid gap-2">
                                <Label htmlFor={`${fieldId}-city`}>City or town</Label>
                                <Input
                                    id={`${fieldId}-city`}
                                    required
                                    autoComplete="address-level2"
                                    placeholder="Philadelphia"
                                    value={draft.city}
                                    onChange={(event) => {
                                        setField('city', event.target.value)
                                    }}
                                />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor={`${fieldId}-address`}>
                                    Street address
                                    <span className="text-muted-foreground font-normal">
                                        (optional)
                                    </span>
                                </Label>
                                <Input
                                    id={`${fieldId}-address`}
                                    autoComplete="street-address"
                                    value={draft.address}
                                    onChange={(event) => {
                                        setField('address', event.target.value)
                                    }}
                                />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor={`${fieldId}-iata`}>
                                    Nearest airport code
                                    <span className="text-muted-foreground font-normal">
                                        (optional)
                                    </span>
                                </Label>
                                <Input
                                    id={`${fieldId}-iata`}
                                    maxLength={3}
                                    placeholder="PHL"
                                    className="uppercase"
                                    aria-invalid={iataInvalid}
                                    aria-describedby={`${fieldId}-iata-hint`}
                                    value={draft.iata}
                                    onChange={(event) => {
                                        setField('iata', event.target.value)
                                    }}
                                />
                                <p
                                    id={`${fieldId}-iata-hint`}
                                    className={
                                        iataInvalid
                                            ? 'text-destructive text-xs'
                                            : 'text-muted-foreground text-xs'
                                    }
                                >
                                    {iataInvalid
                                        ? 'Use the three-letter airport code, e.g. PHL.'
                                        : 'Flight searches depart from here.'}
                                </p>
                            </div>
                            {updateMe.isError && (
                                <p role="alert" className="text-destructive text-sm">
                                    {errorMessage(updateMe.error, "Couldn't save your home")}
                                </p>
                            )}
                        </div>

                        <SheetFooter>
                            <Button type="submit" disabled={updateMe.isPending || iataInvalid}>
                                Save home
                            </Button>
                        </SheetFooter>
                    </form>
                </SheetContent>
            </Sheet>
        </>
    )
}
