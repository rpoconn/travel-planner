import type { PlanningMessage, TravelPlan } from '@/api/types.gen'

// Pure helpers for PlanningPanel: putting the stored transcript and the live turn together,
// and what the composer suggests.

// ----- types -----

/** What the plan is waiting on the user for: a form, choices, a suggestion, or nothing. */
export type Pending = TravelPlan['pending']

// ----- transcript -----

/**
 * The stored transcript followed by the live turn's messages that it doesn't hold yet.
 * Matching by id means a refetch that lands before the turn is dropped never shows a
 * message twice.
 */
export function mergeTranscript(
    history: readonly PlanningMessage[],
    turnMessages: readonly PlanningMessage[],
): PlanningMessage[] {
    const stored = new Set(history.map((message) => message.id))
    return [...history, ...turnMessages.filter((message) => !stored.has(message.id))]
}

/**
 * The last message shows what is still pending: the same question, the same offer, or the
 * reason of the same suggestion (stored as a plain message, since its card isn't).
 */
function showsPending(message: PlanningMessage | undefined, pending: Pending): boolean {
    if (!message || !pending) return false
    const { payload } = message
    switch (pending.type) {
        case 'ask_input':
            return payload?.type === 'ask_input' && payload.slot === pending.slot
        case 'offer_choice':
            return payload?.type === 'offer_choice' && payload.offer_id === pending.offer_id
        case 'review_proposal':
            return message.role === 'assistant' && message.content === pending.prompt
        default:
            return false
    }
}

/**
 * The transcript as shown. A last message that asks the question (or shows the choices, or
 * gives the suggestion's reason) still pending is left out: the form, the list or the card
 * under the transcript shows it instead.
 */
export function visibleMessages(
    messages: readonly PlanningMessage[],
    pending: Pending,
): PlanningMessage[] {
    return showsPending(messages.at(-1), pending) ? messages.slice(0, -1) : [...messages]
}

// ----- carrying on -----

/**
 * Whether to offer a Continue button: the planner isn't waiting on anything, and the trip
 * still needs something. That is how things stand after it answered a question, after a
 * failure, and for a trip whose basics were filled in before any planning.
 */
export function canContinue(plan: TravelPlan): boolean {
    return plan.pending === null && !plan.complete
}

// ----- composer -----

/**
 * The composer's hint: the pending question, choices or suggestion, since typing an answer
 * works too, and that typed edits ("remove the hotel") work in every state.
 */
export function composerPlaceholder(pending: Pending): string {
    switch (pending?.type) {
        case 'ask_input':
            return `${pending.prompt} Answer here or in the form, or ask for a change.`
        case 'offer_choice':
            return 'Pick one above, say which ("the second one"), or ask for a change.'
        case 'review_proposal':
            return "Approve or reject above, or say what you'd rather do."
        default:
            return 'Ask about your trip, or ask for a change…'
    }
}
