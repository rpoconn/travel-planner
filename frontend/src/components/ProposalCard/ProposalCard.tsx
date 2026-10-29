import {
    BedDouble,
    ClipboardList,
    Compass,
    MapPin,
    Pencil,
    Plane,
    SendHorizontal,
    Trash2,
    TrainFront,
    type LucideIcon,
} from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'

import type { ChatProposal, ProposalDecision, ProposedChange } from '@/api/types.gen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { formatDateRange, formatMoney } from '@/lib/format'
import { humanize } from '@/lib/itemMeta'

// The changes an assistant reply proposed, under that reply. Nothing in it is applied until
// the user picks Approve (apply all), Deny (apply none) or Other… (tell the assistant what to
// do instead). Once decided, the card stays in the transcript with its outcome as a badge.

// ----- types -----

export type ProposalAction = ProposalDecision['action']

interface ProposalCardProps {
    proposal: ChatProposal
    /** True while a reply streams: the buttons are inert until it ends. */
    disabled?: boolean
    /** `message` is the "Other…" text, sent with `revise`. */
    onDecide: (action: ProposalAction, message?: string) => void
}

// ----- display helpers -----

const CHANGE_ICONS: Record<string, LucideIcon> = {
    add_flight: Plane,
    add_lodging: BedDouble,
    add_activity: MapPin,
    add_transfer: TrainFront,
    set_plan_details: Compass,
    update_item: Pencil,
    remove_item: Trash2,
}

const STATUS_LABELS: Record<ChatProposal['status'], string> = {
    pending: 'Waiting for you',
    approved: 'Approved',
    denied: 'Denied',
    revised: 'Revised',
    superseded: 'Superseded',
    failed: "Couldn't apply",
}

function text(value: unknown): string | null {
    return typeof value === 'string' && value !== '' ? value : null
}

/** A place argument as a short label: its airport code, else its name. */
function placeText(value: unknown): string | null {
    if (typeof value !== 'object' || value === null) return text(value)
    const { iata, name } = value as { iata?: unknown; name?: unknown }
    return text(iata) ?? text(name)
}

/** "2027-05-06T11:00:00+09:00" -> "2027-05-06 11:00" (the local time, as the model sent it). */
function localTime(value: unknown): string | null {
    const iso = text(value)
    return iso ? iso.slice(0, 16).replace('T', ' ') : null
}

function travelersText(value: unknown): string | null {
    if (typeof value !== 'object' || value === null) return null
    const { adults, children } = value as { adults?: unknown; children?: unknown }
    if (typeof adults !== 'number') return null
    const parts = [`${String(adults)} adult${adults === 1 ? '' : 's'}`]
    if (typeof children === 'number' && children > 0) {
        parts.push(`${String(children)} ${children === 1 ? 'child' : 'children'}`)
    }
    return parts.join(', ')
}

function moneyText(value: unknown): string | null {
    if (typeof value !== 'object' || value === null) return null
    const { amount, currency } = value as { amount?: unknown; currency?: unknown }
    if (typeof amount !== 'string' && typeof amount !== 'number') return null
    return formatMoney({ amount: String(amount), currency: text(currency) ?? undefined })
}

/** An item's cost, "~" when estimated. Staged arguments leave out the default basis, which
 * is "estimate", so only an explicit "exact" drops the "~". */
function costText(value: unknown): string | null {
    const money = moneyText(value)
    if (money === null) return null
    return (value as { basis?: unknown }).basis === 'exact' ? money : `~${money}`
}

/** "Food ≤ $200.00 a person" per spending limit; an empty list clears them. */
function limitsText(value: unknown): string | null {
    if (!Array.isArray(value)) return null
    if (value.length === 0) return 'No spending limits'
    const parts = value.flatMap((limit: unknown) => {
        if (typeof limit !== 'object' || limit === null) return []
        const { category, max_per_person } = limit as {
            category?: unknown
            max_per_person?: unknown
        }
        const name = text(category)
        const cap = moneyText(max_per_person)
        return name && cap ? [`${humanize(name)} ≤ ${cap} a person`] : []
    })
    return parts.length > 0 ? parts.join(', ') : null
}

const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/

function shortValue(value: unknown): string {
    if (typeof value === 'string')
        return ISO_DATE_TIME.test(value) ? (localTime(value) ?? value) : value
    if (typeof value === 'number' || typeof value === 'boolean') return String(value)
    return placeText(value) ?? '…'
}

/** The key details of a change, shown muted under its summary; null when there are none. */
function changeDetails(change: ProposedChange): string | null {
    const args = change.arguments
    const parts: (string | null)[] = []
    switch (change.name) {
        case 'set_plan_details': {
            const origin = placeText(args.origin)
            const destination = placeText(args.destination)
            parts.push(
                text(args.title) && `“${text(args.title) ?? ''}”`,
                destination && `to ${destination}`,
                origin && `from ${origin}`,
                formatDateRange(text(args.start_date), text(args.end_date)),
                travelersText(args.travelers),
                moneyText(args.budget),
                limitsText(args.limits),
            )
            break
        }
        case 'update_item': {
            const changes = (args.changes ?? {}) as Record<string, unknown>
            parts.push(
                ...Object.entries(changes).map(
                    ([field, value]) => `${humanize(field)}: ${shortValue(value)}`,
                ),
            )
            break
        }
        case 'remove_item':
            break
        default: {
            // add_*: where it is and when it ends (the summary already has the start).
            const from = placeText(args.from_place)
            const to = placeText(args.to_place)
            const end = localTime(args.end)
            parts.push(
                from && to ? `${from} → ${to}` : placeText(args.place),
                end && `until ${end}`,
                costText(args.cost),
            )
        }
    }
    const shown = parts.filter((part): part is string => Boolean(part))
    return shown.length > 0 ? shown.join(' · ') : null
}

export function ProposalCard({ proposal, disabled = false, onDecide }: ProposalCardProps) {
    // ----- state -----
    const [revising, setRevising] = useState(false)
    const [message, setMessage] = useState('')
    const pending = proposal.status === 'pending'
    const canRevise = !disabled && message.trim().length > 0

    // ----- event handlers -----
    function sendRevision() {
        if (canRevise) onDecide('revise', message.trim())
    }

    function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        // Same as the composer: Enter sends, Shift+Enter (or an IME confirmation) doesn't.
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            sendRevision()
        }
    }

    // ----- markup -----
    return (
        <Card
            role="region"
            aria-label="Proposed changes"
            data-status={proposal.status}
            size="sm"
            className="border-primary/25 bg-primary/5 ring-primary/20 max-w-[85%]"
        >
            <div className="flex flex-col gap-3 px-4">
                <header className="flex items-center gap-2">
                    <ClipboardList className="text-primary size-4 shrink-0" aria-hidden />
                    <h3 className="text-sm font-medium">Proposed changes</h3>
                    {!pending && (
                        <Badge
                            variant={proposal.status === 'approved' ? 'default' : 'secondary'}
                            className="ml-auto"
                        >
                            {STATUS_LABELS[proposal.status]}
                        </Badge>
                    )}
                </header>

                <ul className="flex flex-col gap-2">
                    {proposal.changes.map((change) => (
                        <ChangeRow key={change.call_id} change={change} />
                    ))}
                </ul>

                {pending && (
                    <div className="flex flex-col gap-2">
                        <div className="flex flex-wrap gap-2">
                            <Button
                                size="sm"
                                disabled={disabled}
                                onClick={() => {
                                    onDecide('approve')
                                }}
                            >
                                Approve
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={disabled}
                                onClick={() => {
                                    onDecide('deny')
                                }}
                            >
                                Deny
                            </Button>
                            <Button
                                size="sm"
                                variant="ghost"
                                disabled={disabled}
                                aria-expanded={revising}
                                onClick={() => {
                                    setRevising((open) => !open)
                                }}
                            >
                                Other…
                            </Button>
                        </div>
                        {revising && (
                            <div className="flex items-end gap-2">
                                <Textarea
                                    aria-label="What would you like instead?"
                                    placeholder="What would you like instead?"
                                    rows={1}
                                    className="bg-card min-h-9"
                                    value={message}
                                    disabled={disabled}
                                    autoFocus
                                    onChange={(event) => {
                                        setMessage(event.target.value)
                                    }}
                                    onKeyDown={handleKeyDown}
                                />
                                <Button
                                    size="icon-sm"
                                    aria-label="Send instead"
                                    disabled={!canRevise}
                                    onClick={sendRevision}
                                >
                                    <SendHorizontal aria-hidden />
                                </Button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </Card>
    )
}

// ----- subcomponents -----

function ChangeRow({ change }: { change: ProposedChange }) {
    const Icon = CHANGE_ICONS[change.name] ?? ClipboardList
    const details = changeDetails(change)
    return (
        <li className="flex items-start gap-2 text-sm">
            <Icon className="text-primary mt-0.5 size-4 shrink-0" aria-hidden />
            <div className="min-w-0">
                <p>{change.summary}</p>
                {details && <p className="text-muted-foreground text-xs">{details}</p>}
            </div>
        </li>
    )
}
