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

/**
 * The transcript as shown. A last message that asks the question still pending is left out:
 * the form under the transcript asks it, with the same words.
 */
export function visibleMessages(
    messages: readonly PlanningMessage[],
    action: NextAction,
): PlanningMessage[] {
    const last = messages.at(-1)
    const asking =
        action.type === 'ask_input' &&
        last?.kind === 'expected_input' &&
        last.payload?.type === 'ask_input' &&
        last.payload.slot === action.slot
    return asking ? messages.slice(0, -1) : [...messages]
}

// ----- composer -----

/** The composer's hint: the pending question, since typing an answer works too. */
export function composerPlaceholder(action: NextAction): string {
    if (action.type === 'ask_input') return `${action.prompt} Type it here, or use the form.`
    return 'Ask anything about your trip…'
}
