// Turns whatever the generated SDK throws into a user-facing message. With throwOnError the
// SDK throws the parsed response body: `{ detail: string }` (ErrorResponse) for our
// HTTPExceptions, `{ detail: [{ msg }] }` for FastAPI validation errors, or an Error from fetch.

const NETWORK_MESSAGE = "Can't reach the server. Is the backend running?"

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null
}

export function errorMessage(error: unknown, fallback = 'Something went wrong'): string {
    if (error instanceof TypeError) return NETWORK_MESSAGE
    if (error instanceof Error) return error.message || fallback
    if (!isRecord(error)) return fallback

    const { detail } = error
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
        const first: unknown = detail[0]
        if (isRecord(first) && typeof first.msg === 'string') return first.msg
    }
    return fallback
}
