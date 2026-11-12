import { Button, Drawer, TextInput } from '@mantine/core'
import { useMutation } from '@tanstack/react-query'
import { IconHome } from '@tabler/icons-react'
import { type SubmitEvent, useState } from 'react'

import { updateMeMutation } from '@/api/@tanstack/react-query.gen'
import type { Place } from '@/api/types.gen'
import { errorMessage } from '@/lib/apiError'
import { setSession, useAuthSession } from '@/lib/auth'

// The header's home button and the drawer that edits it. Home is where new trips start and
// end: the server copies it into a new plan's origin, the itinerary draws "Home" at both
// ends, and its nearest airport (IATA) is where flight searches depart. A user without a
// home gets the drawer opened for them once, non-modal, so the app stays usable behind it.

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
    function openDrawer() {
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
            <Button variant="subtle" leftSection={<IconHome aria-hidden />} onClick={openDrawer}>
                {home ? `Home: ${homeName(home)}` : 'Set home'}
            </Button>

            {/* Non-modal: no overlay, focus and scrolling stay free, and clicking the app
                behind the drawer doesn't dismiss it. */}
            <Drawer.Root
                opened={open}
                onClose={() => {
                    setOpen(false)
                }}
                position="right"
                size="sm"
                closeOnClickOutside={modal}
                trapFocus={modal}
                lockScroll={modal}
            >
                {modal && <Drawer.Overlay />}
                <Drawer.Content>
                    <form onSubmit={handleSubmit} className="flex h-full flex-col">
                        <div className="flex flex-col gap-1.5 p-4 pr-12">
                            <Drawer.Title className="font-heading text-base leading-snug">
                                Where is home?
                            </Drawer.Title>
                            <p className="text-muted-foreground text-sm">
                                New trips start and end here, and the planner makes sure you have a
                                way to get from home to your first stop and back. You can still
                                change where one trip starts in its chat.
                            </p>
                        </div>

                        <div className="flex flex-col gap-4 px-4">
                            <TextInput
                                label="City or town"
                                required
                                autoComplete="address-level2"
                                placeholder="Philadelphia"
                                value={draft.city}
                                onChange={(event) => {
                                    setField('city', event.target.value)
                                }}
                            />
                            <TextInput
                                label={
                                    <>
                                        Street address{' '}
                                        <span className="text-muted-foreground font-normal">
                                            (optional)
                                        </span>
                                    </>
                                }
                                autoComplete="street-address"
                                value={draft.address}
                                onChange={(event) => {
                                    setField('address', event.target.value)
                                }}
                            />
                            {/* The hint turns into the error (and marks the field invalid)
                                while the code isn't three letters. */}
                            <TextInput
                                label={
                                    <>
                                        Nearest airport code{' '}
                                        <span className="text-muted-foreground font-normal">
                                            (optional)
                                        </span>
                                    </>
                                }
                                maxLength={3}
                                placeholder="PHL"
                                classNames={{ input: 'uppercase' }}
                                description={
                                    iataInvalid ? undefined : 'Flight searches depart from here.'
                                }
                                error={
                                    iataInvalid
                                        ? 'Use the three-letter airport code, e.g. PHL.'
                                        : undefined
                                }
                                value={draft.iata}
                                onChange={(event) => {
                                    setField('iata', event.target.value)
                                }}
                            />
                            {updateMe.isError && (
                                <p role="alert" className="text-destructive text-sm">
                                    {errorMessage(updateMe.error, "Couldn't save your home")}
                                </p>
                            )}
                        </div>

                        <div className="mt-auto flex flex-col p-4">
                            <Button
                                type="submit"
                                size="sm"
                                disabled={updateMe.isPending || iataInvalid}
                            >
                                Save home
                            </Button>
                        </div>
                    </form>
                    <Drawer.CloseButton />
                </Drawer.Content>
            </Drawer.Root>
        </>
    )
}
