import { continuePlanning, sendPlanningMessage, submitPlanningInput } from '@/api/sdk.gen'
import type {
    PlanningDoneEvent,
    PlanningErrorEvent,
    PlanningMessageEvent,
    PlanningPlanEvent,
    PlanningStatusEvent,
    PlanningTokenEvent,
    SubmitPlanningInputData,
} from '@/api/types.gen'
import { errorMessage } from '@/lib/apiError'
import { clearSession } from '@/lib/auth'

// Streaming a planning turn over the generated SDK's SSE client: a form answer or pick, free
// text, or continue. hey-api types each stream item as `unknown` (it ignores OpenAPI 3.2
// `itemSchema`), so the event union is rebuilt here from the generated event types and
// every item is narrowed on its `type` tag.

// ----- types -----

export type PlanningEvent =
    | PlanningStatusEvent
    | PlanningTokenEvent
    | PlanningMessageEvent
    | PlanningPlanEvent
    | PlanningDoneEvent
    | PlanningErrorEvent

/** A form answer or skip, as `submitPlanningInput` takes it. */
export type PlanningInputBody = SubmitPlanningInputData['body']

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

const PLANNING_EVENT_TYPES: ReadonlySet<string> = new Set<PlanningEvent['type']>([
    'status',
    'token',
    'message',
    'plan_updated',
    'done',
    'error',
])

/**
 * Narrow one decoded `data:` payload to a PlanningEvent, or null if it isn't one. Only the
 * tag is checked: the payload comes from our own Pydantic-validated backend, so a known tag
 * implies the rest of the shape. Unknown tags (a newer server) are skipped, not fatal.
 */
export function parsePlanningEvent(data: unknown): PlanningEvent | null {
    if (typeof data !== 'object' || data === null) return null
    const { type } = data as { type?: unknown }
    return typeof type === 'string' && PLANNING_EVENT_TYPES.has(type)
        ? (data as PlanningEvent)
        : null
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

/** What the generated SDK's SSE functions take beyond their path and body. */
interface SseRequestOptions {
    signal?: AbortSignal
    fetch: typeof fetch
    sseMaxRetryAttempts: number
    onSseError: (error: unknown) => void
}

/** Starts one SSE request with the given transport options; returns its event stream. */
type StartStream = (options: SseRequestOptions) => Promise<{ stream: AsyncIterable<unknown> }>

/**
 * Run `start` and yield its events (those `parse` recognises) as they stream in. Throws a
 * ChatRequestError (or a fetch TypeError when offline) if the request fails; ends quietly
 * when `signal` aborts.
 */
async function* streamEvents<E>(
    start: StartStream,
    signal: AbortSignal | undefined,
    parse: (data: unknown) => E | null,
): AsyncGenerator<E, void, undefined> {
    // The SSE client reports connection failures through this callback instead of throwing.
    let failure: unknown = null

    const { stream } = await start({
        signal,
        fetch: checkedFetch,
        // Never retry: re-POSTing would store the user's message (or answer) a second time.
        sseMaxRetryAttempts: 1,
        onSseError: (error) => {
            failure = error
        },
    })

    for await (const data of stream) {
        const event = parse(data)
        if (event) yield event
    }

    if (failure === null || signal?.aborted) return
    throw failure instanceof Error ? failure : new ChatRequestError(errorMessage(failure))
}

// ----- streams -----

interface StreamOptions {
    signal?: AbortSignal
}

/** Answer or skip the pending form; yields the turn's events (see streamEvents). */
export function streamPlanningInput(
    planId: string,
    body: PlanningInputBody,
    { signal }: StreamOptions = {},
): AsyncGenerator<PlanningEvent, void, undefined> {
    return streamEvents(
        (options) => submitPlanningInput({ path: { plan_id: planId }, body, ...options }),
        signal,
        parsePlanningEvent,
    )
}

/** Send free text to the planner: read as the pending form's answer, or a question. */
export function streamPlanningChat(
    planId: string,
    content: string,
    { signal }: StreamOptions = {},
): AsyncGenerator<PlanningEvent, void, undefined> {
    return streamEvents(
        (options) =>
            sendPlanningMessage({ path: { plan_id: planId }, body: { content }, ...options }),
        signal,
        parsePlanningEvent,
    )
}

/** Let the planner act with no input: move on, run or retry a search, re-ask the form. */
export function streamPlanningContinue(
    planId: string,
    { signal }: StreamOptions = {},
): AsyncGenerator<PlanningEvent, void, undefined> {
    return streamEvents(
        (options) => continuePlanning({ path: { plan_id: planId }, ...options }),
        signal,
        parsePlanningEvent,
    )
}
