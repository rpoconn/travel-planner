import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'

import { listMessagesOptions } from '@/api/@tanstack/react-query.gen'
import type { TimelineWarning, TravelPlan } from '@/api/types.gen'
import { Composer } from '@/components/Composer/Composer'
import { EmptyChat } from '@/components/EmptyChat/EmptyChat'
import { LiveTurn } from '@/components/LiveTurn/LiveTurn'
import { MessageBubble } from '@/components/MessageBubble/MessageBubble'
import { ProposalCard, type ProposalAction } from '@/components/ProposalCard/ProposalCard'
import { StageControls } from '@/components/StageControls/StageControls'
import { errorMessage } from '@/lib/apiError'
import { applyChatEvent, startTurn, toolsFromTraces, type ChatTurn } from '@/lib/chatTurn'
import { applyPlanSnapshot } from '@/lib/planCache'
import { suggestionsFor } from '@/lib/stages'
import { streamChat, streamDecision, type ChatEvent } from '@/lib/sse'

// The plan's conversation: stored history from the API plus the live turn while a reply
// streams in. Mount with `key={plan.id}` so switching trips resets it and aborts the stream.
// Plan changes the assistant proposes appear as a card under its reply; answering the card
// (Approve / Deny / Other…) is a turn of its own, streamed like a message.

// ----- types -----

/** Commands a sibling (the itinerary's "Fix it" button) can send to the chat. */
export interface ChatPanelHandle {
    /** Ask the assistant to propose a fix for `warning`; ignored while a reply streams. */
    sendFix: (warning: TimelineWarning) => void
}

interface ChatPanelProps {
    plan: TravelPlan
    ref?: Ref<ChatPanelHandle>
    /** Told whenever a reply starts or stops streaming (and `false` on unmount). */
    onBusyChange?: (busy: boolean) => void
}

/** What the transcript shows for a button decision (the server stores the same text). */
const DECISION_TEXT: Record<Exclude<ProposalAction, 'revise'>, string> = {
    approve: 'Approved the proposed changes.',
    deny: 'Denied the proposed changes.',
}

/** Starts a turn's request; the turn loop owns the abort signal. */
type StartTurn = (signal: AbortSignal) => AsyncGenerator<ChatEvent, void, undefined>

export function ChatPanel({ plan, ref, onBusyChange }: ChatPanelProps) {
    // ----- state -----
    const queryClient = useQueryClient()
    const messagesOptions = listMessagesOptions({ path: { plan_id: plan.id } })
    const messagesQuery = useQuery(messagesOptions)

    const [draft, setDraft] = useState('')
    const [turn, setTurn] = useState<ChatTurn | null>(null)
    const [sendError, setSendError] = useState<string | null>(null)
    const abortRef = useRef<AbortController | null>(null)
    const scrollRef = useRef<HTMLDivElement>(null)

    const busy = turn !== null
    const messages = messagesQuery.data ?? []
    // Hide the live turn once the refetched history includes its stored reply (see replyId).
    const showTurn =
        turn !== null &&
        !(turn.replyId !== null && messages.some((message) => message.id === turn.replyId))

    // ----- effects -----
    // Leaving the trip (unmount) stops the stream; the server still finishes and stores it.
    useEffect(
        () => () => {
            abortRef.current?.abort()
        },
        [],
    )

    // Keep the newest message in view as history loads and tokens stream in.
    useEffect(() => {
        const element = scrollRef.current
        if (element) element.scrollTop = element.scrollHeight
    }, [messagesQuery.data, turn])

    // Report streaming to the parent. Derived from `turn` so every way a turn ends (done,
    // failed request, abort on unmount) is covered; the cleanup reports `false` on unmount.
    useEffect(() => {
        onBusyChange?.(busy)
        return () => {
            onBusyChange?.(false)
        }
    }, [busy, onBusyChange])

    // A click on "Fix it" is a command, not state, so it's an imperative handle: sending from
    // an effect on "pending fix" state would double-send under StrictMode.
    useImperativeHandle(ref, () => ({
        sendFix: (warning) => void send(`Fix: ${warning.message}`, warning),
    }))

    // ----- event handlers -----
    async function send(text: string, fix?: TimelineWarning) {
        const content = text.trim()
        if (!content || turn !== null) return
        setDraft('')
        await runTurn(content, (signal) => streamChat(plan.id, content, { signal, fix }), content)
    }

    async function decide(proposalId: string, action: ProposalAction, message?: string) {
        if (turn !== null) return
        const userText = action === 'revise' ? (message ?? '') : DECISION_TEXT[action]
        await runTurn(userText, (signal) =>
            streamDecision(plan.id, proposalId, { action, message }, { signal }),
        )
    }

    /**
     * Stream one turn into `turn`, then refetch the stored history and drop it. If the
     * request fails before any event arrives (nothing was stored), `restoreDraft` goes back
     * into the composer for a retry.
     */
    async function runTurn(userText: string, start: StartTurn, restoreDraft?: string) {
        const controller = new AbortController()
        abortRef.current = controller
        setSendError(null)

        // Folded locally as well as in state so the end-of-stream checks see the final turn.
        let current = startTurn(userText)
        let receivedAny = false
        setTurn(current)

        try {
            for await (const event of start(controller.signal)) {
                receivedAny = true
                if (event.type === 'plan_updated') applyPlanSnapshot(queryClient, event.plan)
                current = applyChatEvent(current, event)
                setTurn(current)
            }
        } catch (error) {
            if (controller.signal.aborted) return
            setSendError(errorMessage(error, "Couldn't send your message"))
            if (!receivedAny) {
                // Same render as the error, so the text is never briefly missing.
                if (restoreDraft !== undefined) setDraft(restoreDraft)
                setTurn(null)
                return
            }
        }
        if (controller.signal.aborted) return

        if (current.status === 'error' && current.replyId === null) {
            // The failure couldn't be stored, so the refetch won't show it: keep it visible.
            setSendError(current.error)
        } else if (current.status === 'streaming') {
            setSendError('The connection dropped before the reply finished.')
        }
        await queryClient.invalidateQueries({ queryKey: messagesOptions.queryKey })
        setTurn(null)
    }

    // ----- markup -----
    return (
        <div className="flex h-full flex-col">
            {/* `*:shrink-0`: entries must scroll, never squeeze. Without it, once the log
                overflows, flexbox shrinks any `overflow-hidden` child (e.g. ProposalCard's
                Card) down to nothing, since such items have no content-based min height. */}
            <div
                ref={scrollRef}
                role="log"
                aria-label="Conversation"
                aria-busy={turn?.status === 'streaming'}
                className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-4 *:shrink-0"
            >
                {/* The chat is now full-width, so keep messages at a readable line length. */}
                <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 *:shrink-0">
                    {messagesQuery.isPending ? (
                        <p className="text-muted-foreground text-sm">Loading conversation…</p>
                    ) : messagesQuery.isError ? (
                        <p role="alert" className="text-destructive text-sm">
                            {errorMessage(messagesQuery.error, "Couldn't load the conversation")}
                        </p>
                    ) : messages.length === 0 && turn === null ? (
                        <EmptyChat
                            stage={plan.stage}
                            suggestions={suggestionsFor(plan)}
                            onPick={(text) => void send(text)}
                        />
                    ) : (
                        <>
                            {messages.map(({ proposal, ...message }) => (
                                <Fragment key={message.id}>
                                    <MessageBubble
                                        role={message.role}
                                        kind={message.kind}
                                        content={message.content}
                                        tools={toolsFromTraces(message.tool_calls)}
                                    />
                                    {proposal && (
                                        <ProposalCard
                                            proposal={proposal}
                                            disabled={busy}
                                            onDecide={(action, text) =>
                                                void decide(proposal.id, action, text)
                                            }
                                        />
                                    )}
                                </Fragment>
                            ))}
                            {showTurn && <LiveTurn turn={turn} />}
                        </>
                    )}
                    {/* The stage decision (Back / Next) is part of the conversation: it
                        follows the latest message and hides while a reply streams. */}
                    {!messagesQuery.isPending && !messagesQuery.isError && !busy && (
                        <StageControls plan={plan} />
                    )}
                </div>
            </div>

            <div className="flex flex-col gap-2 px-4 pt-2 pb-4">
                {sendError && (
                    <p role="alert" className="text-destructive px-2 text-sm">
                        {sendError}
                    </p>
                )}
                <Composer
                    value={draft}
                    onChange={setDraft}
                    onSubmit={(text) => void send(text)}
                    busy={busy}
                />
            </div>
        </div>
    )
}
