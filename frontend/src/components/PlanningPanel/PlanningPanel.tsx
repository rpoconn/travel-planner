import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'

import { getPlanQueryKey, listPlanningMessagesOptions } from '@/api/@tanstack/react-query.gen'
import type { TravelPlan } from '@/api/types.gen'
import { ChoiceList } from '@/components/ChoiceList/ChoiceList'
import { Composer } from '@/components/Composer/Composer'
import { ExpectedInputPanel } from '@/components/ExpectedInputPanel/ExpectedInputPanel'
import { MessageBubble } from '@/components/MessageBubble/MessageBubble'
import { ProposalCard } from '@/components/ProposalCard/ProposalCard'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { applyPlanSnapshot } from '@/lib/planCache'
import { applyPlanningEvent, startPlanningTurn, type PlanningTurn } from '@/lib/planningTurn'
import {
    ChatRequestError,
    streamPlanningChat,
    streamPlanningContinue,
    streamPlanningDecision,
    streamPlanningInput,
    streamPlanningRemove,
    type PlanningEvent,
    type PlanningInputBody,
    type ProposalDecision,
} from '@/lib/sse'

import {
    canContinue,
    composerPlaceholder,
    mergeTranscript,
    visibleMessages,
} from './PlanningPanelService'

// The conversation with the planner: the transcript, then what the plan is waiting on the
// user for (`plan.pending`): a form, choices to pick from, or a suggestion to approve or
// reject; with the composer underneath for free text, which works in every state. The
// planner decides what comes next, so the form and buttons follow the server.
// Every turn (a form answer, a pick, free text, a decision, Continue) streams the messages
// the server stores; once it ends the transcript is refetched. Mount with `key={plan.id}`.
// The itinerary's Remove button and the problems list's "Fix this" run their turns here too,
// through the `ref` handle, so their outcome lands in this transcript.

// ----- types -----

/** What the panel lets its parent do: turns started outside it. */
export interface PlanningPanelHandle {
    /** Take an item off the trip; ignored while a turn is streaming. */
    removeItem: (itemId: string) => void
    /** Ask the planner to work on a problem next; ignored while a turn is streaming. */
    fixProblem: (problemId: string) => void
}

interface PlanningPanelProps {
    plan: TravelPlan
    /** Told whenever a turn starts or stops streaming (and `false` on unmount). */
    onBusyChange?: (busy: boolean) => void
    ref?: Ref<PlanningPanelHandle>
}

/** Starts a turn's request; the turn loop owns the abort signal. */
type StartTurn = (signal: AbortSignal) => AsyncGenerator<PlanningEvent, void, undefined>

export function PlanningPanel({ plan, onBusyChange, ref }: PlanningPanelProps) {
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
    const pending = plan.pending
    const transcript = visibleMessages(
        mergeTranscript(messagesQuery.data ?? [], turn?.messages ?? []),
        pending,
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

    /** The itinerary's Remove button: the item goes, then the planner carries on. */
    async function removeItem(itemId: string) {
        if (busy) return
        await runTurn(null, (signal) => streamPlanningRemove(plan.id, itemId, { signal }))
    }

    /** A suggestion card's Approve or Reject. */
    async function decide(proposalId: string, decision: ProposalDecision) {
        if (busy) return
        await runTurn(null, (signal) =>
            streamPlanningDecision(plan.id, proposalId, decision, { signal }),
        )
    }

    /**
     * Let the planner carry on. With `refresh`, look for other options than the choices
     * shown; with `problemId`, work on that problem first.
     */
    async function continuePlanning(options: { refresh?: boolean; problemId?: string } = {}) {
        if (busy) return
        await runTurn(null, (signal) => streamPlanningContinue(plan.id, { signal, ...options }))
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

    // The parent's handle on the turns above. After them, since it calls them; recreated each
    // render, so each one always sees whether a turn is streaming.
    useImperativeHandle(ref, () => ({
        removeItem: (itemId) => void removeItem(itemId),
        fixProblem: (problemId) => void continuePlanning({ problemId }),
    }))

    // ----- markup -----
    return (
        <div className="flex h-full flex-col">
            {/* `*:shrink-0`: entries keep their height and the log scrolls, rather than squeezing
                them to fit. */}
            <div
                ref={scrollRef}
                role="log"
                aria-label="Conversation"
                aria-busy={turn?.status === 'streaming'}
                className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-4 py-3 *:shrink-0"
            >
                <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 *:shrink-0">
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
                                    payload={message.payload}
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
                                ("Finding flights out…"), or typing dots. */}
                            {turn?.status === 'streaming' && (
                                <MessageBubble
                                    role="assistant"
                                    kind="message"
                                    content={turn.streamingText || (turn.activity ?? '')}
                                    streaming
                                />
                            )}
                            {pending?.type === 'offer_choice' && (
                                <ChoiceList
                                    key={pending.offer_id}
                                    action={pending}
                                    disabled={busy}
                                    onPick={(candidateIds) =>
                                        void submitInput({
                                            type: 'pick',
                                            offer_id: pending.offer_id,
                                            candidate_ids: candidateIds,
                                        })
                                    }
                                    onMore={() => void continuePlanning({ refresh: true })}
                                />
                            )}
                            {pending?.type === 'ask_input' && (
                                <ExpectedInputPanel
                                    // The prompt too: the planner may ask the same slot again
                                    // in other words, which is a new form.
                                    key={`${pending.slot}:${pending.prompt}`}
                                    action={pending}
                                    disabled={busy}
                                    onSubmit={(value) =>
                                        void submitInput({
                                            type: 'answer',
                                            slot: pending.slot,
                                            value,
                                        })
                                    }
                                    onSkip={() =>
                                        void submitInput({ type: 'skip', slot: pending.slot })
                                    }
                                />
                            )}
                            {pending?.type === 'review_proposal' && (
                                <ProposalCard
                                    key={pending.proposal_id}
                                    proposal={pending}
                                    disabled={busy}
                                    onDecide={(decision) =>
                                        void decide(pending.proposal_id, decision)
                                    }
                                />
                            )}
                            {/* Nothing is being asked and the trip isn't done: the planner
                                answered a question, or a turn failed. One button carries on. */}
                            {canContinue(plan) && !busy && (
                                <div className="flex items-center gap-3">
                                    <Button size="sm" onClick={() => void continuePlanning()}>
                                        <ArrowRight aria-hidden />
                                        Continue planning
                                    </Button>
                                </div>
                            )}
                        </>
                    )}
                </div>
            </div>

            <div className="flex flex-col gap-2 px-4 pt-1.5 pb-3">
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
                    placeholder={composerPlaceholder(pending)}
                />
            </div>
        </div>
    )
}
