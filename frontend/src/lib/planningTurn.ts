import type { PlanningMessage } from '@/api/types.gen'
import type { PlanningEvent } from '@/lib/sse'

// The in-flight planning turn, folded from streamed events by a pure reducer. Every message
// the server stores arrives as a `message` event, so the turn holds them until PlanningPanel
// refetches the transcript (which then contains them, and the turn is dropped).

// ----- types -----

export interface PlanningTurn {
    /** Free text the user sent, shown until the server's stored copy arrives. */
    pendingText: string | null
    /** Messages stored during the turn, in order. */
    messages: PlanningMessage[]
    /**
     * The planner's streamed reply so far. Cleared once it is stored as its answer, and
     * dropped when the planner goes on to use a tool instead (a `status` event): that text
     * was it thinking aloud.
     */
    streamingText: string
    /** What the planner is doing, e.g. "Finding flights out…"; cleared by the next message. */
    activity: string | null
    status: 'streaming' | 'done' | 'error'
    error: string | null
    /** Whether the failure was stored (so the refetched transcript shows it). */
    errorStored: boolean
}

// ----- reducer -----

/** A new turn; `pendingText` is the user's free text (null for a form or continue). */
export function startPlanningTurn(pendingText: string | null = null): PlanningTurn {
    return {
        pendingText,
        messages: [],
        streamingText: '',
        activity: null,
        status: 'streaming',
        error: null,
        errorStored: false,
    }
}

export function applyPlanningEvent(turn: PlanningTurn, event: PlanningEvent): PlanningTurn {
    switch (event.type) {
        case 'status':
            return { ...turn, activity: event.text, streamingText: '' }
        case 'token':
            return { ...turn, streamingText: turn.streamingText + event.text }
        case 'message': {
            const { message } = event
            return {
                ...turn,
                messages: [...turn.messages, message],
                // The stored copy replaces the optimistic one…
                pendingText: message.role === 'user' ? null : turn.pendingText,
                // …and the stored answer replaces its streamed text.
                streamingText: message.role === 'assistant' ? '' : turn.streamingText,
                activity: null,
            }
        }
        case 'done':
            return { ...turn, status: 'done', activity: null }
        case 'error':
            return {
                ...turn,
                status: 'error',
                activity: null,
                error: event.detail,
                errorStored: event.message != null,
                messages: event.message ? [...turn.messages, event.message] : turn.messages,
            }
        case 'plan_updated':
            // A side effect on the query cache (lib/planCache.ts), not part of the transcript.
            return turn
    }
}
