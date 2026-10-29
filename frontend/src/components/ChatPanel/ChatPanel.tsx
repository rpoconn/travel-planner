import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { listMessagesOptions } from '@/api/@tanstack/react-query.gen'
import type { TravelPlan } from '@/api/types.gen'
import { Composer } from '@/components/Composer/Composer'
import { MessageBubble } from '@/components/MessageBubble/MessageBubble'
import { errorMessage } from '@/lib/apiError'
import { applyChatEvent, startTurn, toolsFromTraces, type ChatTurn } from '@/lib/chatTurn'
import { applyPlanSnapshot } from '@/lib/planCache'
import { streamChat } from '@/lib/sse'

// The plan's conversation: stored history from the API plus the live turn while a reply
// streams in. Mount with `key={plan.id}` so switching trips resets it and aborts the stream.

// ----- types -----

interface ChatPanelProps {
    plan: TravelPlan
}

function suggestionsFor(plan: TravelPlan): string[] {
    const destination = plan.destination?.city ?? plan.destination?.name
    if (destination) {
        return [
            `Find flights to ${destination}`,
            `Suggest a place to stay in ${destination}`,
            `What should we do on the first day?`,
        ]
    }
    return [
        'I want to spend a week in Tokyo in May',
        'Plan a long weekend in Lisbon',
        'Where is warm and walkable in February?',
    ]
}

export function ChatPanel({ plan }: ChatPanelProps) {
    // ----- state -----
    const queryClient = useQueryClient()
    const messagesOptions = listMessagesOptions({ path: { plan_id: plan.id } })
    const messagesQuery = useQuery(messagesOptions)

    const [draft, setDraft] = useState('')
    const [turn, setTurn] = useState<ChatTurn | null>(null)
    const [sendError, setSendError] = useState<string | null>(null)
    const abortRef = useRef<AbortController | null>(null)
    const scrollRef = useRef<HTMLDivElement>(null)

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

    // ----- actions -----
    async function send(text: string) {
        const content = text.trim()
        if (!content || turn !== null) return

        const controller = new AbortController()
        abortRef.current = controller
        setDraft('')
        setSendError(null)

        // Folded locally as well as in state so the end-of-stream checks see the final turn.
        let current = startTurn(content)
        let receivedAny = false
        setTurn(current)

        try {
            for await (const event of streamChat(plan.id, content, {
                signal: controller.signal,
            })) {
                receivedAny = true
                if (event.type === 'plan_updated') applyPlanSnapshot(queryClient, event.plan)
                current = applyChatEvent(current, event)
                setTurn(current)
            }
        } catch (error) {
            if (controller.signal.aborted) return
            setSendError(errorMessage(error, "Couldn't send your message"))
            if (!receivedAny) {
                // Nothing reached the server's pipeline: give the text back for a retry.
                setDraft(content)
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
    function renderTranscript() {
        if (messagesQuery.isPending) {
            return <p className="text-muted-foreground text-sm">Loading conversation…</p>
        }
        if (messagesQuery.isError) {
            return (
                <p role="alert" className="text-destructive text-sm">
                    {errorMessage(messagesQuery.error, "Couldn't load the conversation")}
                </p>
            )
        }
        if (messages.length === 0 && turn === null) {
            return (
                <EmptyChat suggestions={suggestionsFor(plan)} onPick={(text) => void send(text)} />
            )
        }
        return (
            <>
                {messages.map((message) => (
                    <MessageBubble
                        key={message.id}
                        role={message.role}
                        kind={message.kind}
                        content={message.content}
                        tools={toolsFromTraces(message.tool_calls)}
                    />
                ))}
                {showTurn && <LiveTurn turn={turn} />}
            </>
        )
    }

    return (
        <div className="flex h-full flex-col">
            <div
                ref={scrollRef}
                role="log"
                aria-label="Conversation"
                aria-busy={turn?.status === 'streaming'}
                className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-4"
            >
                {renderTranscript()}
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
                    busy={turn !== null}
                />
            </div>
        </div>
    )
}

// ----- subcomponents -----

function LiveTurn({ turn }: { turn: ChatTurn }) {
    // Once the guardrail says off-topic, the reply renders as the hint card, matching how the
    // stored redirect looks after the history refetch.
    const kind = turn.onTopic === false ? 'redirect' : 'message'
    const streaming = turn.status === 'streaming'
    const hasReply = streaming || turn.text !== '' || turn.tools.length > 0

    return (
        <>
            <MessageBubble role="user" kind={kind} content={turn.userText} />
            {hasReply && (
                <MessageBubble
                    role="assistant"
                    kind={kind}
                    content={turn.text}
                    tools={turn.tools}
                    streaming={streaming}
                />
            )}
            {turn.status === 'error' && turn.error && (
                <MessageBubble role="system" kind="error" content={turn.error} />
            )}
        </>
    )
}

interface EmptyChatProps {
    suggestions: string[]
    onPick: (text: string) => void
}

function EmptyChat({ suggestions, onPick }: EmptyChatProps) {
    return (
        <div className="m-auto flex max-w-sm flex-col items-center gap-4 text-center">
            <div className="bg-accent text-terracotta flex size-12 items-center justify-center rounded-full">
                <Sparkles className="size-6" aria-hidden />
            </div>
            <div>
                <p className="font-heading text-xl">Where to next?</p>
                <p className="text-muted-foreground mt-1 text-sm">
                    Tell me where and when you'd like to go. I'll look things up and build the
                    itinerary on the right as we talk.
                </p>
            </div>
            <ul aria-label="Suggestions" className="flex w-full flex-col gap-2">
                {suggestions.map((suggestion) => (
                    <li key={suggestion}>
                        <button
                            type="button"
                            className="bg-card hover:border-primary/40 hover:bg-primary/5 w-full rounded-xl border px-4 py-2 text-left text-sm transition-colors"
                            onClick={() => {
                                onPick(suggestion)
                            }}
                        >
                            {suggestion}
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    )
}
