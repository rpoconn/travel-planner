import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { getPlanQueryKey, listPlanningMessagesOptions } from '@/api/@tanstack/react-query.gen'
import type { TravelPlan } from '@/api/types.gen'
import { ChoiceList } from '@/components/ChoiceList/ChoiceList'
import { Composer } from '@/components/Composer/Composer'
import { ExpectedInputPanel } from '@/components/ExpectedInputPanel/ExpectedInputPanel'
import { IssuesPanel } from '@/components/IssuesPanel/IssuesPanel'
import { MessageBubble } from '@/components/MessageBubble/MessageBubble'
import { StageBar } from '@/components/StageBar/StageBar'
import { errorMessage } from '@/lib/apiError'
import { applyPlanSnapshot } from '@/lib/planCache'
import { applyPlanningEvent, startPlanningTurn, type PlanningTurn } from '@/lib/planningTurn'
import {
    ChatRequestError,
    streamPlanningChat,
    streamPlanningContinue,
    streamPlanningInput,
    type PlanningEvent,
    type PlanningInputBody,
} from '@/lib/sse'

import { composerPlaceholder, mergeTranscript, visibleMessages } from './PlanningPanelService'

// The rules-driven planning flow: the transcript, then the planner's pending question as a
// form (or its choices to pick from), then the stage bar, with the composer underneath for
// free text. What comes next is
// always `plan.assessment.next_action`, so the form and buttons follow the server.
// Every turn (a form answer, free text, Continue) streams the messages the server stores;
// once it ends the transcript is refetched. Mount with `key={plan.id}`.

// ----- types -----

interface PlanningPanelProps {
    plan: TravelPlan
    /** Told whenever a turn starts or stops streaming (and `false` on unmount). */
    onBusyChange?: (busy: boolean) => void
}

/** Starts a turn's request; the turn loop owns the abort signal. */
type StartTurn = (signal: AbortSignal) => AsyncGenerator<PlanningEvent, void, undefined>

export function PlanningPanel({ plan, onBusyChange }: PlanningPanelProps) {
    // ----- state -----
    const queryClient = useQueryClient()
    const messagesOptions = listPlanningMessagesOptions({ path: { plan_id: plan.id } })
    const messagesQuery = useQuery(messagesOptions)

    const [draft, setDraft] = useState('')
    const [turn, setTurn] = useState<PlanningTurn | null>(null)
    const [sendError, setSendError] = useState<string | null>(null)
    const abortRef = useRef<AbortController | null>(null)
    const scrollRef = useRef<HTMLDivElement>(null)

    const busy = turn !== null
    const action = plan.assessment.next_action
    const transcript = visibleMessages(
        mergeTranscript(messagesQuery.data ?? [], turn?.messages ?? []),
        action,
    )

    // ----- effects -----
    // Leaving the trip (unmount) stops the stream; the server still finishes the turn.
    useEffect(
        () => () => {
            abortRef.current?.abort()
        },
        [],
    )

    // Keep the newest entry in view as messages arrive.
    useEffect(() => {
        const element = scrollRef.current
        if (element) element.scrollTop = element.scrollHeight
    }, [messagesQuery.data, turn])

    // Report streaming to the parent; the cleanup reports `false` on unmount.
    useEffect(() => {
        onBusyChange?.(busy)
        return () => {
            onBusyChange?.(false)
        }
    }, [busy, onBusyChange])

    // ----- event handlers -----
    async function send(text: string) {
        const content = text.trim()
        if (!content || busy) return
        setDraft('')
        await runTurn(
            content,
            (signal) => streamPlanningChat(plan.id, content, { signal }),
            content,
        )
    }

    async function submitInput(body: PlanningInputBody) {
        if (busy) return
        await runTurn(null, (signal) => streamPlanningInput(plan.id, body, { signal }))
    }

    async function continuePlanning() {
        if (busy) return
        await runTurn(null, (signal) => streamPlanningContinue(plan.id, { signal }))
    }

    /**
     * Stream one turn into `turn`, then refetch the transcript and drop it. If the request
     * fails before any event arrives (nothing was stored), `restoreDraft` goes back into the
     * composer.
     */
    async function runTurn(pendingText: string | null, start: StartTurn, restoreDraft?: string) {
        const controller = new AbortController()
        abortRef.current = controller
        setSendError(null)

        // Folded locally as well as in state so the end-of-stream checks see the final turn.
        let current = startPlanningTurn(pendingText)
        let receivedAny = false
        setTurn(current)

        try {
            for await (const event of start(controller.signal)) {
                receivedAny = true
                if (event.type === 'plan_updated' || event.type === 'done') {
                    applyPlanSnapshot(queryClient, event.plan)
                }
                current = applyPlanningEvent(current, event)
                setTurn(current)
            }
        } catch (error) {
            if (controller.signal.aborted) return
            setSendError(errorMessage(error, "Couldn't reach the planner"))
            if (error instanceof ChatRequestError && error.status === 409) {
                // The form was for a question the plan has moved past: show the current one.
                void queryClient.invalidateQueries({
                    queryKey: getPlanQueryKey({ path: { plan_id: plan.id } }),
                })
            }
            if (!receivedAny) {
                if (restoreDraft !== undefined) setDraft(restoreDraft)
                setTurn(null)
                return
            }
        }
        if (controller.signal.aborted) return

        if (current.status === 'error' && !current.errorStored) {
            // The failure couldn't be stored, so the refetch won't show it: keep it visible.
            setSendError(current.error)
        } else if (current.status === 'streaming') {
            setSendError('The connection dropped before the planner finished.')
        }
        await queryClient.invalidateQueries({ queryKey: messagesOptions.queryKey })
        setTurn(null)
    }

    // ----- markup -----
    return (
        <div className="flex h-full flex-col">
            {/* `*:shrink-0`: entries scroll, never squeeze (see ChatPanel). */}
            <div
                ref={scrollRef}
                role="log"
                aria-label="Conversation"
                aria-busy={turn?.status === 'streaming'}
                className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-4 *:shrink-0"
            >
                <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 *:shrink-0">
                    {messagesQuery.isPending ? (
                        <p className="text-muted-foreground text-sm">Loading conversation…</p>
                    ) : messagesQuery.isError ? (
                        <p role="alert" className="text-destructive text-sm">
                            {errorMessage(messagesQuery.error, "Couldn't load the conversation")}
                        </p>
                    ) : (
                        <>
                            {transcript.map((message) => (
                                <MessageBubble
                                    key={message.id}
                                    role={message.role}
                                    kind={message.kind}
                                    content={message.content}
                                />
                            ))}
                            {turn?.pendingText && (
                                <MessageBubble
                                    role="user"
                                    kind="message"
                                    content={turn.pendingText}
                                />
                            )}
                            {/* The planner at work: a streamed answer, what it's doing
                                ("Find flights out…"), or typing dots. */}
                            {turn?.status === 'streaming' && (
                                <MessageBubble
                                    role="assistant"
                                    kind="message"
                                    content={turn.streamingText || (turn.activity ?? '')}
                                    streaming
                                />
                            )}
                            {action.type === 'offer_choice' && (
                                <ChoiceList
                                    key={action.offer_id}
                                    action={action}
                                    disabled={busy}
                                    onPick={(candidateIds) =>
                                        void submitInput({
                                            type: 'pick',
                                            offer_id: action.offer_id,
                                            candidate_ids: candidateIds,
                                        })
                                    }
                                />
                            )}
                            {action.type === 'ask_input' && (
                                <ExpectedInputPanel
                                    key={action.slot}
                                    action={action}
                                    disabled={busy}
                                    onSubmit={(value) =>
                                        void submitInput({
                                            type: 'answer',
                                            slot: action.slot,
                                            value,
                                        })
                                    }
                                    onSkip={() =>
                                        void submitInput({ type: 'skip', slot: action.slot })
                                    }
                                />
                            )}
                            {/* At review there's nothing left to ask: what's left to fix. */}
                            {plan.stage === 'review' && (
                                <IssuesPanel plan={plan} inline disabled={busy} />
                            )}
                            <StageBar
                                plan={plan}
                                disabled={busy}
                                onContinue={() => void continuePlanning()}
                            />
                        </>
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
                    placeholder={composerPlaceholder(action)}
                />
            </div>
        </div>
    )
}
