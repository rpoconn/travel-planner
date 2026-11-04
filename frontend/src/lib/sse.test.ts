import { describe, expect, it, vi } from 'vitest'

import { client } from '@/api/client.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import { chatMessage, PLAN_ID, planningMessage, tokenResponse, travelPlan } from '@/test/fixtures'
import { jsonResponse, sseResponse } from '@/test/sse'

import {
    ChatRequestError,
    parseChatEvent,
    parsePlanningEvent,
    streamChat,
    streamDecision,
    streamPlanningChat,
    streamPlanningContinue,
    streamPlanningInput,
    type ChatEvent,
    type PlanningEvent,
} from './sse'

// ----- setup -----

const EVENTS: ChatEvent[] = [
    { type: 'guardrail', on_topic: true, topic: 'flights' },
    { type: 'token', text: 'Looking ' },
    { type: 'tool_call', call_id: 'c1', name: 'web_search', arguments: { query: 'SFO NRT' } },
    {
        type: 'tool_result',
        call_id: 'c1',
        name: 'web_search',
        ok: true,
        summary: 'Searched',
        proposed: false,
        sources: [],
    },
    { type: 'plan_updated', plan: travelPlan({ version: 2 }) },
    { type: 'token', text: 'done — “ünïcode” ✈' },
    { type: 'done', message: chatMessage() },
]

async function collect<E>(stream: AsyncGenerator<E>): Promise<E[]> {
    const events: E[] = []
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

    it('sends a fix only when one is given', async () => {
        const fix = { kind: 'no_lodging' as const, message: 'No lodging', date: null, item_ids: [] }
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValueOnce(sseResponse(EVENTS))
            .mockResolvedValueOnce(sseResponse(EVENTS))
        await collect(streamChat(PLAN_ID, 'Fix: No lodging', { fix }))
        await collect(streamChat(PLAN_ID, 'hi'))

        const [withFix, without] = fetchSpy.mock.calls.map((call) => call[0] as Request)
        expect(await withFix?.json()).toEqual({ content: 'Fix: No lodging', fix })
        expect(Object.keys((await without?.json()) as object)).toEqual(['content'])
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

describe('streamDecision', () => {
    it('POSTs the decision to the proposal and yields its events', async () => {
        client.setConfig({ auth: () => 'test-token' })
        const fetchSpy = mockFetch(sseResponse(EVENTS))

        const events = await collect(
            streamDecision(PLAN_ID, 'p1', { action: 'revise', message: 'A ryokan instead' }),
        )

        expect(events).toEqual(EVENTS)
        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.method).toBe('POST')
        expect(request.url).toBe(`http://api.test/plans/${PLAN_ID}/chat/proposals/p1/decision`)
        expect(request.headers.get('Authorization')).toBe('Bearer test-token')
        expect(await request.json()).toEqual({ action: 'revise', message: 'A ryokan instead' })
    })

    it('reports a stale card (409) as a ChatRequestError with the detail', async () => {
        mockFetch(jsonResponse({ detail: 'This proposal was already denied' }, 409))

        await expect(
            collect(streamDecision(PLAN_ID, 'p1', { action: 'approve' })),
        ).rejects.toMatchObject({ status: 409, message: 'This proposal was already denied' })
    })
})

// ----- planning flow -----

const PLANNING_EVENTS: PlanningEvent[] = [
    {
        type: 'message',
        message: planningMessage({ role: 'user', kind: 'input', content: 'Lisbon' }),
    },
    { type: 'plan_updated', plan: travelPlan({ version: 2 }) },
    { type: 'status', text: 'Find flights out…' },
    { type: 'token', text: 'Lovely ' },
    { type: 'done', plan: travelPlan({ version: 2 }) },
]

describe('parsePlanningEvent', () => {
    it('accepts the planning event types only', () => {
        expect(parsePlanningEvent({ type: 'status', text: 'x' })).toEqual({
            type: 'status',
            text: 'x',
        })
        expect(parsePlanningEvent({ type: 'guardrail', on_topic: true })).toBeNull()
        expect(parsePlanningEvent(null)).toBeNull()
    })
})

describe('planning streams', () => {
    it('POSTs a form answer to the inputs route and yields its events', async () => {
        client.setConfig({ auth: () => 'test-token' })
        const fetchSpy = mockFetch(sseResponse([...PLANNING_EVENTS, { type: 'guardrail' }]))
        const body = {
            type: 'answer' as const,
            slot: 'destination' as const,
            value: { destination: { name: 'Lisbon' } },
        }

        const events = await collect(streamPlanningInput(PLAN_ID, body))

        expect(events).toEqual(PLANNING_EVENTS)
        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.method).toBe('POST')
        expect(request.url).toBe(`http://api.test/plans/${PLAN_ID}/planning/inputs`)
        expect(request.headers.get('Authorization')).toBe('Bearer test-token')
        expect(await request.json()).toEqual(body)
    })

    it('POSTs free text to the planning chat', async () => {
        const fetchSpy = mockFetch(sseResponse(PLANNING_EVENTS))

        await collect(streamPlanningChat(PLAN_ID, 'two adults'))

        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.url).toBe(`http://api.test/plans/${PLAN_ID}/planning/chat`)
        expect(await request.json()).toEqual({ content: 'two adults' })
    })

    it('POSTs continue with no body', async () => {
        const fetchSpy = mockFetch(sseResponse(PLANNING_EVENTS))

        await collect(streamPlanningContinue(PLAN_ID))

        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.method).toBe('POST')
        expect(request.url).toBe(`http://api.test/plans/${PLAN_ID}/planning/continue`)
    })

    it('reports a stale form (409) with the detail', async () => {
        mockFetch(
            jsonResponse({ detail: "That question isn't the one being asked any more." }, 409),
        )

        await expect(
            collect(streamPlanningInput(PLAN_ID, { type: 'skip', slot: 'budget' })),
        ).rejects.toMatchObject({ status: 409 })
    })
})
