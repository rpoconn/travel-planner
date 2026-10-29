import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { getPlanQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import type { ChatMessage, TimelineWarning } from '@/api/types.gen'
import { chatMessage, chatProposal, PLAN_ID, sdkOk, travelPlan } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'
import { controlledSseResponse, jsonResponse, sseResponse } from '@/test/sse'

import { ChatPanel, type ChatPanelHandle } from './ChatPanel'

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
            proposed: false,
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
            proposed: false,
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

describe('ChatPanel sendFix', () => {
    const WARNING: TimelineWarning = {
        kind: 'overlap',
        message: 'Senso-ji overlaps Tsukiji market',
        date: '2027-05-02',
        item_ids: ['i1', 'i2'],
    }

    function renderWithHandle() {
        const ref = createRef<ChatPanelHandle>()
        const onBusyChange = vi.fn<(busy: boolean) => void>()
        renderWithQuery(<ChatPanel ref={ref} plan={travelPlan()} onBusyChange={onBusyChange} />)
        return { ref, onBusyChange }
    }

    it('sends the visible text with the warning and reports busy while it streams', async () => {
        mockHistory([])
        const stream = controlledSseResponse()
        const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        const { ref, onBusyChange } = renderWithHandle()
        await screen.findByRole('list', { name: 'Suggestions' })

        act(() => {
            ref.current?.sendFix(WARNING)
        })

        expect(within(log()).getByText('Fix: Senso-ji overlaps Tsukiji market')).toBeInTheDocument()
        expect(onBusyChange).toHaveBeenLastCalledWith(true)
        expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
        await waitFor(() => {
            expect(fetchSpy).toHaveBeenCalledTimes(1)
        })
        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(await request.json()).toEqual({
            content: 'Fix: Senso-ji overlaps Tsukiji market',
            fix: WARNING,
        })

        // A second click while the reply streams is ignored.
        act(() => {
            ref.current?.sendFix(WARNING)
        })
        expect(fetchSpy).toHaveBeenCalledTimes(1)

        stream.push({ type: 'done', message: REPLY })
        stream.close()
        await waitFor(() => {
            expect(onBusyChange).toHaveBeenLastCalledWith(false)
        })
    })

    it('reports not busy and keeps the text when the request fails', async () => {
        mockHistory([])
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            jsonResponse({ detail: 'Plan not found' }, 404),
        )
        const { ref, onBusyChange } = renderWithHandle()
        await screen.findByRole('list', { name: 'Suggestions' })

        act(() => {
            ref.current?.sendFix(WARNING)
        })

        expect(await screen.findByRole('alert')).toHaveTextContent('Plan not found')
        expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue(
            'Fix: Senso-ji overlaps Tsukiji market',
        )
        expect(onBusyChange).toHaveBeenLastCalledWith(false)
    })
})

describe('ChatPanel proposals', () => {
    const PROPOSING = chatMessage({
        id: 'a1',
        content: "I've proposed Hotel Okura.",
        tool_calls: [
            {
                call_id: 'c1',
                name: 'add_lodging',
                arguments: {},
                ok: true,
                summary: 'Proposed: Add lodging “Hotel Okura” on 2027-05-02 15:00',
                proposed: true,
            },
        ],
        proposal: chatProposal({ message_id: 'a1' }),
    })
    const DECISION_URL = `http://api.test/plans/${PLAN_ID}/chat/proposals/proposal-1/decision`
    const card = () => screen.getByRole('region', { name: 'Proposed changes' })

    it('shows the card under the reply that proposed it', async () => {
        mockHistory([USER_MESSAGE, PROPOSING])
        renderPanel()

        expect(await screen.findByRole('region', { name: 'Proposed changes' })).toHaveTextContent(
            'Add lodging “Hotel Okura” on 2027-05-02 15:00',
        )
        expect(
            screen.getByText('Proposed: Add lodging “Hotel Okura” on 2027-05-02 15:00')
                .parentElement,
        ).toHaveAttribute('data-status', 'proposed')
    })

    it('approves: posts the decision, streams the applied changes and updates the plan', async () => {
        const applied = chatMessage({
            id: 'a2',
            content: 'Hotel Okura is on your plan. Flights next?',
            tool_calls: [
                {
                    call_id: 'c9',
                    name: 'add_lodging',
                    arguments: {},
                    ok: true,
                    summary: 'Added lodging “Hotel Okura”',
                    proposed: false,
                },
            ],
        })
        const approvedHistory = [
            USER_MESSAGE,
            { ...PROPOSING, proposal: chatProposal({ status: 'approved' }) },
            chatMessage({
                id: 'u2',
                role: 'user',
                kind: 'decision',
                content: 'Approved the proposed changes.',
            }),
            applied,
        ]
        mockHistory([USER_MESSAGE, PROPOSING], approvedHistory)
        const stream = controlledSseResponse()
        const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        const { user, queryClient } = renderPanel()

        await user.click(await screen.findByRole('button', { name: 'Approve' }))

        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.url).toBe(DECISION_URL)
        expect(await request.json()).toEqual({ action: 'approve' })
        // The decision shows as the user's turn, and the card is inert while it streams.
        expect(within(log()).getByText('Approved the proposed changes.')).toBeInTheDocument()
        expect(within(card()).getByRole('button', { name: 'Deny' })).toBeDisabled()

        stream.push({ type: 'guardrail', on_topic: true, topic: 'proposal decision' })
        stream.push({ type: 'tool_call', call_id: 'c9', name: 'add_lodging', arguments: {} })
        stream.push({
            type: 'tool_result',
            call_id: 'c9',
            name: 'add_lodging',
            ok: true,
            summary: 'Added lodging “Hotel Okura”',
            proposed: false,
        })
        expect(await screen.findByText('Added lodging “Hotel Okura”')).toBeInTheDocument()
        const updated = travelPlan({ version: 2 })
        stream.push({ type: 'plan_updated', plan: updated })
        await waitFor(() => {
            expect(
                queryClient.getQueryData(getPlanQueryKey({ path: { plan_id: PLAN_ID } })),
            ).toEqual(updated)
        })
        stream.push({ type: 'done', message: applied })
        stream.close()

        // The refetched history shows the card as approved, without buttons.
        expect(await within(card()).findByText('Approved')).toBeInTheDocument()
        expect(within(card()).queryByRole('button')).not.toBeInTheDocument()
        expect(screen.getAllByText('Approved the proposed changes.')).toHaveLength(1)
    })

    it('denies with one click', async () => {
        mockHistory([USER_MESSAGE, PROPOSING])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', message: chatMessage({ id: 'a2' }) }]))
        const { user } = renderPanel()

        await user.click(await screen.findByRole('button', { name: 'Deny' }))

        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.url).toBe(DECISION_URL)
        expect(await request.json()).toEqual({ action: 'deny' })
    })

    it('sends "Other…" text as a revision and shows it as the user\'s message', async () => {
        mockHistory([USER_MESSAGE, PROPOSING])
        const stream = controlledSseResponse()
        const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        const { user } = renderPanel()

        await user.click(await screen.findByRole('button', { name: 'Other…' }))
        await user.type(
            screen.getByRole('textbox', { name: 'What would you like instead?' }),
            'A ryokan instead{Enter}',
        )

        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(await request.json()).toEqual({ action: 'revise', message: 'A ryokan instead' })
        const bubbles = within(log()).getAllByText('A ryokan instead')
        expect(bubbles.some((element) => element.closest('[data-role="user"]'))).toBe(true)
        expect(screen.getByRole('textbox', { name: 'What would you like instead?' })).toBeDisabled()
        stream.close()
    })

    it('shows the new card on the live turn once the reply is done', async () => {
        mockHistory([])
        const stream = controlledSseResponse()
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        const { user } = renderPanel()
        await screen.findByRole('list', { name: 'Suggestions' })

        await sendText(user, 'Hotel Okura May 2-6')
        stream.push({ type: 'token', text: "I've proposed Hotel Okura." })
        await screen.findByText("I've proposed Hotel Okura.")
        expect(screen.queryByRole('region', { name: 'Proposed changes' })).not.toBeInTheDocument()

        stream.push({ type: 'done', message: PROPOSING })
        const live = await screen.findByRole('region', { name: 'Proposed changes' })
        expect(within(live).getByRole('button', { name: 'Approve' })).toBeDisabled()
        stream.close()
    })

    it('reports a stale card and keeps the transcript', async () => {
        mockHistory([USER_MESSAGE, PROPOSING])
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            jsonResponse({ detail: 'This proposal was already superseded' }, 409),
        )
        const { user } = renderPanel()

        await user.click(await screen.findByRole('button', { name: 'Approve' }))

        expect(await screen.findByRole('alert')).toHaveTextContent(
            'This proposal was already superseded',
        )
        expect(within(log()).queryByText('Approved the proposed changes.')).not.toBeInTheDocument()
    })
})
