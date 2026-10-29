import { describe, expect, it, vi } from 'vitest'

import { client } from '@/api/client.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import { chatMessage, PLAN_ID, tokenResponse, travelPlan } from '@/test/fixtures'
import { jsonResponse, sseResponse } from '@/test/sse'

import { ChatRequestError, parseChatEvent, streamChat, type ChatEvent } from './sse'

// ----- setup -----

const EVENTS: ChatEvent[] = [
    { type: 'guardrail', on_topic: true, topic: 'flights' },
    { type: 'token', text: 'Looking ' },
    { type: 'tool_call', call_id: 'c1', name: 'web_search', arguments: { query: 'SFO NRT' } },
    { type: 'tool_result', call_id: 'c1', name: 'web_search', ok: true, summary: 'Searched' },
    { type: 'plan_updated', plan: travelPlan({ version: 2 }) },
    { type: 'token', text: 'done — “ünïcode” ✈' },
    { type: 'done', message: chatMessage() },
]

async function collect(stream: AsyncGenerator<ChatEvent>): Promise<ChatEvent[]> {
    const events: ChatEvent[] = []
    for await (const event of stream) events.push(event)
    return events
}

function mockFetch(response: Response | Error) {
    const spy = vi.spyOn(globalThis, 'fetch')
    if (response instanceof Error) spy.mockRejectedValue(response)
    else spy.mockResolvedValue(response)
    return spy
}

// ----- tests -----

describe('parseChatEvent', () => {
    it('accepts known event types', () => {
        expect(parseChatEvent({ type: 'token', text: 'hi' })).toEqual({ type: 'token', text: 'hi' })
    })

    it('rejects non-objects and unknown types', () => {
        expect(parseChatEvent('data')).toBeNull()
        expect(parseChatEvent(null)).toBeNull()
        expect(parseChatEvent({ text: 'no type' })).toBeNull()
        expect(parseChatEvent({ type: 'heartbeat' })).toBeNull()
    })
})

describe('streamChat', () => {
    it('POSTs the message with the bearer token and yields every event in order', async () => {
        client.setConfig({ auth: () => 'test-token' })
        const fetchSpy = mockFetch(sseResponse(EVENTS))

        const events = await collect(streamChat(PLAN_ID, 'Find flights'))

        expect(events).toEqual(EVENTS)
        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.method).toBe('POST')
        expect(request.url).toBe(`http://api.test/plans/${PLAN_ID}/chat`)
        expect(request.headers.get('Authorization')).toBe('Bearer test-token')
        expect(await request.json()).toEqual({ content: 'Find flights' })
    })

    it('reassembles frames split across network chunks', async () => {
        // 7-byte chunks cut through `data:` prefixes, JSON and multi-byte characters.
        mockFetch(sseResponse(EVENTS, { chunkSize: 7 }))

        expect(await collect(streamChat(PLAN_ID, 'hi'))).toEqual(EVENTS)
    })

    it('skips events it does not recognize', async () => {
        mockFetch(sseResponse([{ type: 'heartbeat' }, { type: 'token', text: 'ok' }]))

        expect(await collect(streamChat(PLAN_ID, 'hi'))).toEqual([{ type: 'token', text: 'ok' }])
    })

    it('throws the API detail for an HTTP error and does not retry', async () => {
        const fetchSpy = mockFetch(jsonResponse({ detail: 'Plan not found' }, 404))

        const error: unknown = await collect(streamChat(PLAN_ID, 'hi')).catch((e: unknown) => e)

        expect(error).toBeInstanceOf(ChatRequestError)
        expect(error).toMatchObject({ message: 'Plan not found', status: 404 })
        expect(fetchSpy).toHaveBeenCalledTimes(1)
    })

    it('reports validation errors by their first message', async () => {
        mockFetch(
            jsonResponse({ detail: [{ msg: 'String should have at most 4000 characters' }] }, 422),
        )

        await expect(collect(streamChat(PLAN_ID, 'x'))).rejects.toThrow(
            'String should have at most 4000 characters',
        )
    })

    it('ends the session on a 401', async () => {
        setSessionFromToken(tokenResponse())
        mockFetch(jsonResponse({ detail: 'Not authenticated' }, 401))

        await expect(collect(streamChat(PLAN_ID, 'hi'))).rejects.toThrow('Not authenticated')
        expect(getSession()).toBeNull()
    })

    it('rethrows network failures', async () => {
        mockFetch(new TypeError('Failed to fetch'))

        await expect(collect(streamChat(PLAN_ID, 'hi'))).rejects.toThrow(TypeError)
    })

    it('ends quietly when aborted', async () => {
        const controller = new AbortController()
        controller.abort()
        mockFetch(new DOMException('Aborted', 'AbortError'))

        expect(await collect(streamChat(PLAN_ID, 'hi', { signal: controller.signal }))).toEqual([])
    })
})
