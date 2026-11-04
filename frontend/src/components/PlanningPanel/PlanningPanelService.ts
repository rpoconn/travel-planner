import type { PlanningMessage, TravelPlan } from '@/api/types.gen'

// Pure helpers for PlanningPanel: putting the stored transcript and the live turn together,
// and what the composer suggests.

// ----- types -----

export type NextAction = TravelPlan['assessment']['next_action']

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

/** The last message shows what is still pending: the same question, or the same offer. */
function showsPending(message: PlanningMessage | undefined, action: NextAction): boolean {
    const payload = message?.payload
    if (action.type === 'ask_input') {
        return payload?.type === 'ask_input' && payload.slot === action.slot
    }
    if (action.type === 'offer_choice') {
        return payload?.type === 'offer_choice' && payload.offer_id === action.offer_id
    }
    return false
}

/**
 * The transcript as shown. A last message that asks the question (or shows the choices)
 * still pending is left out: the form or the list under the transcript shows it instead.
 */
export function visibleMessages(
    messages: readonly PlanningMessage[],
    action: NextAction,
): PlanningMessage[] {
    return showsPending(messages.at(-1), action) ? messages.slice(0, -1) : [...messages]
}

// ----- composer -----

/** The composer's hint: the pending question or choices, since typing an answer works too. */
export function composerPlaceholder(action: NextAction): string {
    if (action.type === 'ask_input') return `${action.prompt} Type it here, or use the form.`
    if (action.type === 'offer_choice') return 'Pick one above, or say which ("the second one").'
    return 'Ask anything about your trip…'
}
