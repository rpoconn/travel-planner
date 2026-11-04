import { describe, expect, it, vi } from 'vitest'

import { client } from '@/api/client.gen'
import { getSession, setSessionFromToken } from '@/lib/auth'
import { PLAN_ID, planningMessage, tokenResponse, travelPlan } from '@/test/fixtures'
import { jsonResponse, sseResponse } from '@/test/sse'

import {
    ChatRequestError,
    parsePlanningEvent,
    streamPlanningChat,
    streamPlanningContinue,
    streamPlanningInput,
    type PlanningEvent,
} from './sse'

// ----- setup -----

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
        expect(parsePlanningEvent('data')).toBeNull()
        expect(parsePlanningEvent({ text: 'no type' })).toBeNull()
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

describe('transport', () => {
    it('reassembles frames split across network chunks', async () => {
        // 7-byte chunks cut through `data:` prefixes, JSON and multi-byte characters.
        const events: PlanningEvent[] = [...PLANNING_EVENTS, { type: 'token', text: '“ünïcode” ✈' }]
        mockFetch(sseResponse(events, { chunkSize: 7 }))

        expect(await collect(streamPlanningChat(PLAN_ID, 'hi'))).toEqual(events)
    })

    it('skips events it does not recognize', async () => {
        mockFetch(sseResponse([{ type: 'heartbeat' }, { type: 'token', text: 'ok' }]))

        expect(await collect(streamPlanningChat(PLAN_ID, 'hi'))).toEqual([
            { type: 'token', text: 'ok' },
        ])
    })

    it('throws the API detail for an HTTP error and does not retry', async () => {
        const fetchSpy = mockFetch(jsonResponse({ detail: 'Plan not found' }, 404))

        const error: unknown = await collect(streamPlanningChat(PLAN_ID, 'hi')).catch(
            (e: unknown) => e,
        )

        expect(error).toBeInstanceOf(ChatRequestError)
        expect(error).toMatchObject({ message: 'Plan not found', status: 404 })
        expect(fetchSpy).toHaveBeenCalledTimes(1)
    })

    it('reports validation errors by their first message', async () => {
        mockFetch(
            jsonResponse({ detail: [{ msg: 'String should have at most 4000 characters' }] }, 422),
        )

        await expect(collect(streamPlanningChat(PLAN_ID, 'x'))).rejects.toThrow(
            'String should have at most 4000 characters',
        )
    })

    it('ends the session on a 401', async () => {
        setSessionFromToken(tokenResponse())
        mockFetch(jsonResponse({ detail: 'Not authenticated' }, 401))

        await expect(collect(streamPlanningChat(PLAN_ID, 'hi'))).rejects.toThrow(
            'Not authenticated',
        )
        expect(getSession()).toBeNull()
    })

    it('rethrows network failures', async () => {
        mockFetch(new TypeError('Failed to fetch'))

        await expect(collect(streamPlanningChat(PLAN_ID, 'hi'))).rejects.toThrow(TypeError)
    })

    it('ends quietly when aborted', async () => {
        const controller = new AbortController()
        controller.abort()
        mockFetch(new DOMException('Aborted', 'AbortError'))

        expect(
            await collect(streamPlanningChat(PLAN_ID, 'hi', { signal: controller.signal })),
        ).toEqual([])
    })
})
