import { ClipboardList, SendHorizontal } from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'

import type { ChatProposal, ProposalDecision } from '@/api/types.gen'
import { ProposalChangeRow } from '@/components/ProposalChangeRow/ProposalChangeRow'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'

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

// ----- constants -----

const STATUS_LABELS: Record<ChatProposal['status'], string> = {
    pending: 'Waiting for you',
    approved: 'Approved',
    denied: 'Denied',
    revised: 'Revised',
    superseded: 'Superseded',
    failed: "Couldn't apply",
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
                        <ProposalChangeRow key={change.call_id} change={change} />
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
