import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { getPlanQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import type { ChatMessage } from '@/api/types.gen'
import { chatMessage, PLAN_ID, sdkOk, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'
import { controlledSseResponse, jsonResponse, sseResponse } from '@/test/sse'

import { ChatPanel } from './ChatPanel'

// ----- setup -----
// History comes from a spied `listMessages`; the chat POST goes through the real streamChat +
// generated SSE client, with only `fetch` faked.

const USER_MESSAGE = chatMessage({ id: 'u1', role: 'user', content: 'Find a hotel in Tokyo' })
const REPLY = chatMessage({
    id: 'a1',
    content: 'I added Hotel Okura for three nights.',
    tool_calls: [
        {
            call_id: 'c1',
            name: 'add_lodging',
            arguments: {},
            ok: true,
            summary: 'Added lodging “Hotel Okura”',
        },
    ],
})

function mockHistory(...responses: ChatMessage[][]) {
    const spy = vi.spyOn(sdk, 'listMessages')
    for (const messages of responses) spy.mockResolvedValueOnce(sdkOk(messages))
    // Any later refetch keeps returning the last history.
    spy.mockResolvedValue(sdkOk(responses.at(-1) ?? []))
    return spy
}

function renderPanel() {
    const user = userEvent.setup()
    const result = renderWithQuery(<ChatPanel plan={travelPlan()} />)
    return { ...result, user }
}

async function sendText(user: ReturnType<typeof userEvent.setup>, text: string) {
    await user.type(screen.getByRole('textbox', { name: 'Message' }), text)
    await user.click(screen.getByRole('button', { name: 'Send message' }))
}

const log = () => screen.getByRole('log', { name: 'Conversation' })

// ----- tests -----

describe('ChatPanel', () => {
    it('renders the stored conversation with tool chips and redirects', async () => {
        mockHistory([
            USER_MESSAGE,
            REPLY,
            chatMessage({ id: 'u2', role: 'user', kind: 'redirect', content: 'Tell me a joke' }),
            chatMessage({ id: 'a2', kind: 'redirect', content: 'Back to Tokyo!' }),
        ])

        renderPanel()

        expect(await screen.findByText('Find a hotel in Tokyo')).toBeInTheDocument()
        expect(screen.getByText('Added lodging “Hotel Okura”')).toBeInTheDocument()
        expect(screen.getByRole('complementary', { name: 'Off-topic reply' })).toHaveTextContent(
            'Back to Tokyo!',
        )
    })

    it('offers suggestions on an empty chat and sends the one picked', async () => {
        mockHistory([], [USER_MESSAGE, REPLY])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', message: REPLY }]))
        const { user } = renderPanel()

        // travelPlan() has destination Tokyo, so suggestions name it.
        await user.click(await screen.findByRole('button', { name: 'Find flights to Tokyo' }))

        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(await request.json()).toEqual({ content: 'Find flights to Tokyo' })
        expect(await screen.findByText(REPLY.content)).toBeInTheDocument()
    })

    it('streams a reply: tokens, tool chips, plan updates, then the stored history', async () => {
        const history = mockHistory([], [USER_MESSAGE, REPLY])
        const stream = controlledSseResponse()
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        const { user, queryClient } = renderPanel()
        await screen.findByRole('list', { name: 'Suggestions' })

        await sendText(user, 'Find a hotel in Tokyo')

        // The user's message shows at once, with a typing indicator and sending locked.
        expect(within(log()).getByText('Find a hotel in Tokyo')).toBeInTheDocument()
        expect(screen.getByRole('status', { name: 'Assistant is typing' })).toBeInTheDocument()
        expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue('')
        expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()

        stream.push({ type: 'guardrail', on_topic: true, topic: 'hotels' })
        stream.push({ type: 'token', text: 'Let me look' })
        expect(await screen.findByText('Let me look')).toBeInTheDocument()

        stream.push({
            type: 'tool_call',
            call_id: 'c1',
            name: 'web_search',
            arguments: { query: 'Tokyo hotels' },
        })
        expect(await screen.findByText('Searching the web for “Tokyo hotels”…')).toBeInTheDocument()
        stream.push({
            type: 'tool_result',
            call_id: 'c1',
            name: 'web_search',
            ok: true,
            summary: 'Searched the web for “Tokyo hotels”',
        })
        expect(await screen.findByText('Searched the web for “Tokyo hotels”')).toBeInTheDocument()

        const updated = travelPlan({ version: 2, title: 'Tokyo with a hotel' })
        stream.push({ type: 'plan_updated', plan: updated })
        await waitFor(() => {
            expect(
                queryClient.getQueryData(getPlanQueryKey({ path: { plan_id: PLAN_ID } })),
            ).toEqual(updated)
        })

        stream.push({ type: 'done', message: REPLY })
        stream.close()

        // The stored history replaces the live turn without showing the reply twice.
        await waitFor(() => {
            expect(history).toHaveBeenCalledTimes(2)
        })
        expect(await screen.findByText('Added lodging “Hotel Okura”')).toBeInTheDocument()
        expect(screen.getAllByText(REPLY.content)).toHaveLength(1)
        expect(screen.getAllByText('Find a hotel in Tokyo')).toHaveLength(1)
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
        })
        await user.type(screen.getByRole('textbox', { name: 'Message' }), 'Thanks')
        expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled()
    })

    it('renders an off-topic reply as a hint card while it streams', async () => {
        mockHistory([])
        const stream = controlledSseResponse()
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        const { user } = renderPanel()
        await screen.findByRole('list', { name: 'Suggestions' })

        await sendText(user, 'What is the capital of France?')
        stream.push({ type: 'guardrail', on_topic: false, topic: 'geography' })
        stream.push({ type: 'token', text: 'Paris! Now, about Tokyo…' })

        expect(
            await screen.findByRole('complementary', { name: 'Off-topic reply' }),
        ).toHaveTextContent('Paris! Now, about Tokyo…')
        stream.close()
    })

    it('restores the text and shows the reason when the request fails', async () => {
        mockHistory([])
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            jsonResponse({ detail: 'Plan not found' }, 404),
        )
        const { user } = renderPanel()
        await screen.findByRole('list', { name: 'Suggestions' })

        await sendText(user, 'Hello?')

        expect(await screen.findByRole('alert')).toHaveTextContent('Plan not found')
        expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue('Hello?')
        expect(within(log()).queryByText('Hello?')).not.toBeInTheDocument()
    })

    it('shows a failure the server stored, via the refreshed history', async () => {
        const notice = chatMessage({
            id: 'sys1',
            role: 'system',
            kind: 'error',
            content: 'The local model is not responding.',
        })
        mockHistory([], [chatMessage({ id: 'u1', role: 'user', content: 'Hi' }), notice])
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            sseResponse([
                { type: 'guardrail', on_topic: true, topic: 'greeting' },
                { type: 'error', detail: notice.content, message: notice },
            ]),
        )
        const { user } = renderPanel()
        await screen.findByRole('list', { name: 'Suggestions' })

        await sendText(user, 'Hi')

        await waitFor(() => {
            expect(screen.getAllByText('The local model is not responding.')).toHaveLength(1)
        })
        // Stored, so it's part of the transcript rather than a banner.
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('keeps an unsaved failure visible as a banner', async () => {
        mockHistory([], [chatMessage({ id: 'u1', role: 'user', content: 'Hi' })])
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            sseResponse([{ type: 'error', detail: 'Something went wrong.', message: null }]),
        )
        const { user } = renderPanel()
        await screen.findByRole('list', { name: 'Suggestions' })

        await sendText(user, 'Hi')

        expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong.')
    })
})
