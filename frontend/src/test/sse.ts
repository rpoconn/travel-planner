// Fake server-sent-event responses for chat tests. Each event is framed like the backend's
// (`data: <json>` + blank line, no `event:` name).

const encoder = new TextEncoder()

export function sseFrame(event: unknown): string {
    return `data: ${JSON.stringify(event)}\n\n`
}

function sseHeaders(): HeadersInit {
    return { 'Content-Type': 'text/event-stream' }
}

/** A complete stream of `events`, optionally re-chunked to cut frames at odd places. */
export function sseResponse(
    events: readonly unknown[],
    { chunkSize }: { chunkSize?: number } = {},
) {
    const text = events.map(sseFrame).join('')
    const chunks =
        chunkSize === undefined
            ? [text]
            : Array.from({ length: Math.ceil(text.length / chunkSize) }, (_, index) =>
                  text.slice(index * chunkSize, (index + 1) * chunkSize),
              )
    const body = new ReadableStream<Uint8Array>({
        start(controller) {
            for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
            controller.close()
        },
    })
    return new Response(body, { status: 200, headers: sseHeaders() })
}

/** A stream the test feeds event by event, to observe the UI mid-reply. */
export function controlledSseResponse() {
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({
        start(streamController) {
            controller = streamController
        },
    })
    return {
        response: new Response(body, { status: 200, headers: sseHeaders() }),
        push(event: unknown) {
            controller.enqueue(encoder.encode(sseFrame(event)))
        },
        close() {
            controller.close()
        },
    }
}

export function jsonResponse(body: unknown, status: number): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
    })
}
