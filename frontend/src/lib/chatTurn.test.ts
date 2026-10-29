import { describe, expect, it } from 'vitest'

import { chatMessage, travelPlan } from '@/test/fixtures'

import {
    applyChatEvent,
    startTurn,
    toolRunningLabel,
    toolsFromTraces,
    type ChatTurn,
} from './chatTurn'
import type { ChatEvent } from './sse'

function fold(events: ChatEvent[], turn: ChatTurn = startTurn('Find a hotel')): ChatTurn {
    return events.reduce(applyChatEvent, turn)
}

describe('applyChatEvent', () => {
    it('starts empty and streaming', () => {
        expect(startTurn('hi')).toEqual({
            userText: 'hi',
            onTopic: null,
            text: '',
            tools: [],
            status: 'streaming',
            error: null,
            replyId: null,
        })
    })

    it('records the guardrail verdict and appends tokens', () => {
        const turn = fold([
            { type: 'guardrail', on_topic: true, topic: 'hotels' },
            { type: 'token', text: 'Let me ' },
            { type: 'token', text: 'look.' },
        ])

        expect(turn.onTopic).toBe(true)
        expect(turn.text).toBe('Let me look.')
    })

    it('tracks a tool from running to its outcome by call id', () => {
        const running = fold([
            {
                type: 'tool_call',
                call_id: 'c1',
                name: 'web_search',
                arguments: { query: 'ryokan' },
            },
            { type: 'tool_call', call_id: 'c2', name: 'add_lodging', arguments: {} },
        ])
        expect(running.tools).toEqual([
            {
                callId: 'c1',
                name: 'web_search',
                status: 'running',
                summary: 'Searching the web for “ryokan”…',
            },
            {
                callId: 'c2',
                name: 'add_lodging',
                status: 'running',
                summary: 'Adding a place to stay…',
            },
        ])

        const finished = fold(
            [
                {
                    type: 'tool_result',
                    call_id: 'c2',
                    name: 'add_lodging',
                    ok: false,
                    summary: 'Bad dates',
                },
                {
                    type: 'tool_result',
                    call_id: 'c1',
                    name: 'web_search',
                    ok: true,
                    summary: 'Searched',
                },
            ],
            running,
        )
        expect(finished.tools.map((tool) => [tool.status, tool.summary])).toEqual([
            ['ok', 'Searched'],
            ['failed', 'Bad dates'],
        ])
    })

    it('ignores plan snapshots (handled by the query cache)', () => {
        const turn = startTurn('hi')
        expect(applyChatEvent(turn, { type: 'plan_updated', plan: travelPlan() })).toBe(turn)
    })

    it('ends with the stored reply text and id', () => {
        const turn = fold([
            { type: 'token', text: 'partial' },
            { type: 'done', message: chatMessage({ id: 'm9', content: 'Final answer' }) },
        ])

        expect(turn).toMatchObject({ status: 'done', text: 'Final answer', replyId: 'm9' })
    })

    it('ends with an error, keeping the stored notice id when there is one', () => {
        const stored = fold([
            {
                type: 'error',
                detail: 'Model unavailable',
                message: chatMessage({ id: 'sys1', role: 'system', kind: 'error' }),
            },
        ])
        expect(stored).toMatchObject({
            status: 'error',
            error: 'Model unavailable',
            replyId: 'sys1',
        })

        const unsaved = fold([{ type: 'error', detail: 'Boom', message: null }])
        expect(unsaved.replyId).toBeNull()
    })
})

describe('labels', () => {
    it('describes running tools', () => {
        expect(toolRunningLabel('web_search', {})).toBe('Searching the web…')
        expect(toolRunningLabel('remove_item', { item_id: 'x' })).toBe('Removing an item…')
        expect(toolRunningLabel('something_new', {})).toBe('Working…')
    })

    it('maps stored tool traces to chips', () => {
        expect(
            toolsFromTraces([
                {
                    call_id: 'c1',
                    name: 'add_flight',
                    arguments: {},
                    ok: true,
                    summary: 'Added flight',
                },
            ]),
        ).toEqual([{ callId: 'c1', name: 'add_flight', status: 'ok', summary: 'Added flight' }])
    })
})
