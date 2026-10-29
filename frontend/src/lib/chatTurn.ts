import type { ToolTrace } from '@/api/types.gen'
import type { ChatEvent } from '@/lib/sse'

// The in-flight chat exchange, folded from streamed events by a pure reducer. Once the turn
// ends, ChatPanel refetches the stored history and drops the turn.

// ----- types -----

export interface ToolActivity {
    callId: string
    name: string
    status: 'running' | 'ok' | 'failed'
    /** "Searching the web…" while running, then the server's outcome summary. */
    summary: string
}

export interface ChatTurn {
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
}

// ----- labels -----

const RUNNING_LABELS: Record<string, string> = {
    add_flight: 'Adding a flight…',
    add_lodging: 'Adding a place to stay…',
    add_activity: 'Adding an activity…',
    add_transfer: 'Adding a transfer…',
    update_item: 'Updating the itinerary…',
    remove_item: 'Removing an item…',
    set_plan_details: 'Updating trip details…',
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

export function toolsFromTraces(traces: readonly ToolTrace[]): ToolActivity[] {
    return traces.map((trace) => ({
        callId: trace.call_id,
        name: trace.name,
        status: trace.ok ? 'ok' : 'failed',
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
                        ? { ...tool, status: event.ok ? 'ok' : 'failed', summary: event.summary }
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
