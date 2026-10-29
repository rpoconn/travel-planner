import type { ChatProposal, ToolTrace } from '@/api/types.gen'
import type { ChatEvent } from '@/lib/sse'

// The in-flight chat exchange, folded from streamed events by a pure reducer. Once the turn
// ends, ChatPanel refetches the stored history and drops the turn.

// ----- types -----

export interface ToolActivity {
    callId: string
    name: string
    /** `proposed`: a plan change staged onto the reply's proposal card, not applied yet. */
    status: 'running' | 'ok' | 'proposed' | 'failed'
    /** "Searching the web…" while running, then the server's outcome summary. */
    summary: string
}

export interface ChatTurn {
    /** The user's message, or the stored text of their decision on a proposal card. */
    userText: string
    /** The guardrail verdict; null until it arrives. false = the reply is a redirect. */
    onTopic: boolean | null
    text: string
    tools: ToolActivity[]
    status: 'streaming' | 'done' | 'error'
    error: string | null
    /**
     * Id of the stored reply (or stored error notice) once the turn ends. ChatPanel hides the
     * live turn as soon as the refetched history contains it, so it never shows twice.
     */
    replyId: string | null
    /** The changes the reply proposed, from the stored reply; null until `done`. */
    proposal: ChatProposal | null
}

// ----- labels -----

// Write tools only stage a change while the assistant replies (and apply it once the user
// approves), so the labels stay neutral about which is happening.
const RUNNING_LABELS: Record<string, string> = {
    add_round_trip: 'Preparing flights out and home…',
    add_flight: 'Preparing a flight…',
    add_lodging: 'Preparing a place to stay…',
    add_activity: 'Preparing an activity…',
    add_transfer: 'Preparing a transfer…',
    update_item: 'Preparing an itinerary change…',
    remove_item: 'Preparing a removal…',
    set_plan_details: 'Preparing trip details…',
}

/** Chip text while a tool runs, e.g. "Searching the web for “ryokan in Kyoto”…". */
export function toolRunningLabel(name: string, args: Record<string, unknown>): string {
    if (name === 'web_search') {
        return typeof args.query === 'string' && args.query
            ? `Searching the web for “${args.query}”…`
            : 'Searching the web…'
    }
    return RUNNING_LABELS[name] ?? 'Working…'
}

function resultStatus(result: { ok: boolean; proposed: boolean }): ToolActivity['status'] {
    if (!result.ok) return 'failed'
    return result.proposed ? 'proposed' : 'ok'
}

export function toolsFromTraces(traces: readonly ToolTrace[]): ToolActivity[] {
    return traces.map((trace) => ({
        callId: trace.call_id,
        name: trace.name,
        status: resultStatus(trace),
        summary: trace.summary,
    }))
}

// ----- reducer -----

export function startTurn(userText: string): ChatTurn {
    return {
        userText,
        onTopic: null,
        text: '',
        tools: [],
        status: 'streaming',
        error: null,
        replyId: null,
        proposal: null,
    }
}

export function applyChatEvent(turn: ChatTurn, event: ChatEvent): ChatTurn {
    switch (event.type) {
        case 'guardrail':
            return { ...turn, onTopic: event.on_topic }
        case 'token':
            return { ...turn, text: turn.text + event.text }
        case 'tool_call':
            return {
                ...turn,
                tools: [
                    ...turn.tools,
                    {
                        callId: event.call_id,
                        name: event.name,
                        status: 'running',
                        summary: toolRunningLabel(event.name, event.arguments),
                    },
                ],
            }
        case 'tool_result':
            return {
                ...turn,
                tools: turn.tools.map((tool) =>
                    tool.callId === event.call_id
                        ? { ...tool, status: resultStatus(event), summary: event.summary }
                        : tool,
                ),
            }
        case 'done':
            // The stored reply is authoritative (e.g. the server's fallback for an empty one).
            return {
                ...turn,
                text: event.message.content,
                status: 'done',
                replyId: event.message.id,
                proposal: event.message.proposal,
            }
        case 'error':
            return {
                ...turn,
                status: 'error',
                error: event.detail,
                replyId: event.message?.id ?? null,
            }
        case 'plan_updated':
            // A side effect on the query cache (lib/planCache.ts), not part of the transcript.
            return turn
    }
}
