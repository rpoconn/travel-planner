import { sendMessage } from '@/api/sdk.gen'
import type {
    DoneEvent,
    ErrorEvent,
    GuardrailEvent,
    PlanUpdatedEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
} from '@/api/types.gen'
import { errorMessage } from '@/lib/apiError'
import { clearSession } from '@/lib/auth'

// Chat streaming over the generated SDK's SSE client. hey-api types each stream item as
// `unknown` (it ignores OpenAPI 3.2 `itemSchema`), so the event union is rebuilt here from
// the generated event types and every item is narrowed on its `type` tag.

// ----- types -----

export type ChatEvent =
    | GuardrailEvent
    | TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | PlanUpdatedEvent
    | DoneEvent
    | ErrorEvent

/** The request itself failed (404, 422, 401, network) before any event arrived. */
export class ChatRequestError extends Error {
    readonly status: number | undefined

    constructor(message: string, status?: number) {
        super(message)
        this.name = 'ChatRequestError'
        this.status = status
    }
}

// ----- parsing -----

const CHAT_EVENT_TYPES: ReadonlySet<string> = new Set<ChatEvent['type']>([
    'guardrail',
    'token',
    'tool_call',
    'tool_result',
    'plan_updated',
    'done',
    'error',
])

/**
 * Narrow one decoded `data:` payload to a ChatEvent, or null if it isn't one. Only the tag
 * is checked: the payload comes from our own Pydantic-validated backend, so a known tag
 * implies the rest of the shape. Unknown tags (a newer server) are skipped, not fatal.
 */
export function parseChatEvent(data: unknown): ChatEvent | null {
    if (typeof data !== 'object' || data === null) return null
    const { type } = data as { type?: unknown }
    return typeof type === 'string' && CHAT_EVENT_TYPES.has(type) ? (data as ChatEvent) : null
}

// ----- transport -----

/**
 * fetch for the SSE client that turns HTTP errors into a ChatRequestError carrying the API's
 * `detail` (the generated client only reports "SSE failed: 404"). A 401 also ends the session,
 * mirroring the response interceptor regular SDK calls get in lib/apiClient.ts.
 */
const checkedFetch: typeof fetch = async (input, init) => {
    const response = await fetch(input, init)
    if (response.ok) return response

    if (response.status === 401) clearSession()
    let body: unknown = null
    try {
        body = await response.json()
    } catch {
        // Non-JSON error body (proxy page, empty): fall back to the status line.
    }
    throw new ChatRequestError(
        errorMessage(body, `Request failed (${String(response.status)})`),
        response.status,
    )
}

export interface StreamChatOptions {
    signal?: AbortSignal
}

/**
 * Send `content` to the plan's chat and yield its events as they stream in. Throws a
 * ChatRequestError (or a fetch TypeError when offline) if the request fails; ends quietly
 * when `signal` aborts.
 */
export async function* streamChat(
    planId: string,
    content: string,
    { signal }: StreamChatOptions = {},
): AsyncGenerator<ChatEvent, void, undefined> {
    // The SSE client reports connection failures through this callback instead of throwing.
    let failure: unknown = null

    const { stream } = await sendMessage({
        path: { plan_id: planId },
        body: { content },
        signal,
        fetch: checkedFetch,
        // Never retry: re-POSTing would store the user's message a second time.
        sseMaxRetryAttempts: 1,
        onSseError: (error) => {
            failure = error
        },
    })

    for await (const data of stream) {
        const event = parseChatEvent(data)
        if (event) yield event
    }

    if (failure === null || signal?.aborted) return
    throw failure instanceof Error ? failure : new ChatRequestError(errorMessage(failure))
}
