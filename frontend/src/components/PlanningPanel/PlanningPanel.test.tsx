import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { getPlanQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import type { PlanningMessage, TravelPlan } from '@/api/types.gen'
import {
    ASK_DATES,
    ASK_DESTINATION,
    OFFER_FLIGHTS_OUT,
    PLAN_ID,
    planAsking,
    planChoosing,
    planningMessage,
    planReviewing,
    sdkOk,
    SUGGESTION,
    travelPlan,
} from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'
import { controlledSseResponse, jsonResponse, sseResponse } from '@/test/sse'

import { PlanningPanel, type PlanningPanelHandle } from './PlanningPanel'

// ----- setup -----
// The transcript comes from a spied `listPlanningMessages`; turns go through the real
// planning streams and the generated SSE client, with only `fetch` faked.

const ANSWERED = planningMessage({
    id: 'm1',
    role: 'user',
    kind: 'input',
    content: 'Lisbon, Portugal',
    payload: { type: 'answer', slot: 'destination', value: {}, skipped: false },
})
const ASKED_DATES = planningMessage({
    id: 'm2',
    kind: 'expected_input',
    content: 'When are you going?',
    payload: ASK_DATES,
})

function mockTranscript(...responses: PlanningMessage[][]) {
    const spy = vi.spyOn(sdk, 'listPlanningMessages')
    for (const messages of responses) spy.mockResolvedValueOnce(sdkOk(messages))
    spy.mockResolvedValue(sdkOk(responses.at(-1) ?? []))
    return spy
}

function renderPanel(plan: TravelPlan) {
    const user = userEvent.setup()
    const result = renderWithQuery(<PlanningPanel plan={plan} />)
    return { ...result, user }
}

const log = () => screen.getByRole('log', { name: 'Conversation' })

async function sentRequest(spy: { mock: { calls: unknown[][] } }) {
    const request = spy.mock.calls[0]?.[0] as Request
    return { url: request.url, body: (await request.json()) as unknown }
}

// ----- tests -----

describe('PlanningPanel', () => {
    it('shows the transcript and asks the pending question as a form', async () => {
        mockTranscript([
            ANSWERED,
            planningMessage({ id: 'm4', kind: 'nudge', content: 'Which dates?' }),
            ASKED_DATES,
        ])

        renderPanel(planAsking(ASK_DATES))

        expect(await within(log()).findByText('Lisbon, Portugal')).toBeInTheDocument()
        expect(screen.getByRole('complementary', { name: 'Back to the question' })).toBeVisible()
        // The form asks the question, so the stored copy of it isn't repeated above it.
        expect(screen.getByRole('form', { name: 'When are you going?' })).toBeInTheDocument()
        expect(screen.getAllByText('When are you going?')).toHaveLength(1)
        expect(screen.getByRole('textbox', { name: 'Message' })).toHaveAttribute(
            'placeholder',
            'When are you going? Answer here or in the form, or ask for a change.',
        )
    })

    it('shows what a typed edit changed as a list card', async () => {
        mockTranscript([
            planningMessage({ id: 'm5', role: 'user', content: 'remove the hotel' }),
            planningMessage({
                id: 'm6',
                kind: 'changes',
                content: 'Removed “Riverside Hotel”.',
                payload: {
                    type: 'changes',
                    applied: [
                        { name: 'remove_item', summary: 'Removed “Riverside Hotel”', item_id: 'a' },
                    ],
                    rejected: [],
                    left_open: [],
                    clarification: null,
                },
            }),
        ])

        renderPanel(planAsking(ASK_DATES))

        expect(await screen.findByRole('region', { name: 'Trip updated' })).toHaveTextContent(
            'Removed “Riverside Hotel”',
        )
    })

    it('submits the form as a planning input and applies the new plan', async () => {
        const transcript = mockTranscript([], [ANSWERED, ASKED_DATES])
        const next = planAsking(ASK_DATES, { version: 2, title: 'Trip to Lisbon' })
        const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            sseResponse([
                { type: 'message', message: ANSWERED },
                { type: 'plan_updated', plan: next },
                { type: 'message', message: ASKED_DATES },
                { type: 'done', plan: next },
            ]),
        )
        const { user, queryClient } = renderPanel(planAsking(ASK_DESTINATION))

        await user.type(await screen.findByRole('textbox', { name: 'Destination' }), 'Lisbon')
        await user.click(screen.getByRole('button', { name: 'Continue' }))

        expect(await within(log()).findByText('Lisbon, Portugal')).toBeInTheDocument()
        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/inputs`,
            body: {
                type: 'answer',
                slot: 'destination',
                value: { destination: { name: 'Lisbon' } },
            },
        })
        expect(queryClient.getQueryData(getPlanQueryKey({ path: { plan_id: PLAN_ID } }))).toEqual(
            next,
        )
        await waitFor(() => {
            expect(transcript).toHaveBeenCalledTimes(2)
        })
    })

    it('skips an optional question', async () => {
        mockTranscript([])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const { user } = renderPanel(
            planAsking({ ...ASK_DATES, slot: 'budget', optional: true, prompt: 'Budget?' }),
        )

        await user.click(await screen.findByRole('button', { name: 'Skip' }))

        expect((await sentRequest(fetchSpy)).body).toEqual({ type: 'skip', slot: 'budget' })
    })

    it('shows the pending choices once, and sends a pick', async () => {
        mockTranscript([
            planningMessage({
                id: 'm5',
                kind: 'choices',
                content: 'Pick your flight out.',
                payload: OFFER_FLIGHTS_OUT,
            }),
        ])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const { user } = renderPanel(planChoosing(OFFER_FLIGHTS_OUT))

        const choices = await screen.findByRole('region', { name: 'Pick your flight out.' })
        // The list shows the choices, so the stored copy of its prompt isn't repeated.
        expect(screen.getAllByText('Pick your flight out.')).toHaveLength(1)
        expect(screen.getByRole('textbox', { name: 'Message' })).toHaveAttribute(
            'placeholder',
            'Pick one above, say which ("the second one"), or ask for a change.',
        )
        await user.click(within(choices).getByRole('button', { name: 'Pick Example Air EX 102' }))

        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/inputs`,
            body: { type: 'pick', offer_id: OFFER_FLIGHTS_OUT.offer_id, candidate_ids: ['2'] },
        })
    })

    it('asks for more options than the choices shown', async () => {
        mockTranscript([])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const { user } = renderPanel(planChoosing(OFFER_FLIGHTS_OUT))

        await user.click(await screen.findByRole('button', { name: 'More options' }))

        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/continue`,
            body: { refresh: true, problem_id: null },
        })
    })

    it('shows a suggestion once, and sends the decision', async () => {
        mockTranscript([planningMessage({ id: 'm8', content: SUGGESTION.prompt })])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const { user } = renderPanel(planReviewing())

        const card = await screen.findByRole('region', { name: 'Suggested changes' })
        // The card gives the reason, so the stored copy of it isn't repeated above it.
        expect(screen.getAllByText('A quieter second day.')).toHaveLength(1)
        expect(card).toHaveTextContent('Remove “Dinner at Ramiro”')
        expect(screen.getByRole('textbox', { name: 'Message' })).toHaveAttribute(
            'placeholder',
            "Approve or reject above, or say what you'd rather do.",
        )
        await user.click(within(card).getByRole('button', { name: 'Approve' }))

        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/proposals/${SUGGESTION.proposal_id}/decision`,
            body: { decision: 'approve' },
        })
    })

    it('rejects a suggestion', async () => {
        mockTranscript([])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const { user } = renderPanel(planReviewing())

        await user.click(await screen.findByRole('button', { name: 'Reject' }))

        expect((await sentRequest(fetchSpy)).body).toEqual({ decision: 'reject' })
    })

    it('sends free text, showing it while the planner works', async () => {
        mockTranscript([])
        const stream = controlledSseResponse()
        const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream.response)
        const { user } = renderPanel(planAsking(ASK_DESTINATION))

        await user.type(await screen.findByRole('textbox', { name: 'Message' }), 'Lisbon please')
        await user.click(screen.getByRole('button', { name: 'Send message' }))

        expect(await within(log()).findByText('Lisbon please')).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
        // What the planner says before it uses a tool is dropped once it does.
        stream.push({ type: 'token', text: 'Let me check.' })
        expect(await within(log()).findByText('Let me check.')).toBeInTheDocument()
        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/chat`,
            body: { content: 'Lisbon please' },
        })
        stream.push({ type: 'status', text: 'Thinking…' })
        expect(await within(log()).findByText('Thinking…')).toBeInTheDocument()
        expect(within(log()).queryByText('Let me check.')).not.toBeInTheDocument()
        stream.push({ type: 'done', plan: planAsking(ASK_DESTINATION) })
        stream.close()
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
        })
    })

    it("runs an item's removal as a turn, showing what the trip needs", async () => {
        const removed = planningMessage({
            id: 'm7',
            kind: 'changes',
            content: 'Removed “Hotel Okura”. The trip needs a place to stay May 2 to May 5 now.',
            payload: {
                type: 'changes',
                applied: [{ name: 'remove_item', summary: 'Removed “Hotel Okura”', item_id: 'h' }],
                rejected: [],
                left_open: [
                    'The trip needs a place to stay May 2 to May 5 now. Pick another place to ' +
                        "stay, or tell me what you'd like instead",
                ],
                clarification: null,
            },
        })
        mockTranscript([], [removed])
        const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            sseResponse([
                { type: 'message', message: removed },
                { type: 'done', plan: planAsking(ASK_DESTINATION) },
            ]),
        )
        const ref = createRef<PlanningPanelHandle>()
        renderWithQuery(<PlanningPanel plan={planAsking(ASK_DESTINATION)} ref={ref} />)
        await screen.findByRole('textbox', { name: 'Message' })

        ref.current?.removeItem('item-1')

        expect(await screen.findByRole('region', { name: 'Trip updated' })).toHaveTextContent(
            'Pick another place to stay',
        )
        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/removals`,
            body: { item_id: 'item-1' },
        })
    })

    it('asks the planner to work on a problem', async () => {
        mockTranscript([])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const ref = createRef<PlanningPanelHandle>()
        renderWithQuery(<PlanningPanel plan={travelPlan()} ref={ref} />)
        await screen.findByRole('textbox', { name: 'Message' })

        ref.current?.fixProblem('missing_return')

        await waitFor(() => {
            expect(fetchSpy).toHaveBeenCalled()
        })
        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/continue`,
            body: { refresh: false, problem_id: 'missing_return' },
        })
    })

    it('offers to carry on when nothing is being asked', async () => {
        mockTranscript([])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const { user } = renderPanel(travelPlan())

        await user.click(await screen.findByRole('button', { name: 'Continue planning' }))

        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/continue`,
            body: { refresh: false, problem_id: null },
        })
    })

    it('has nothing to carry on with once the trip is complete', async () => {
        mockTranscript([planningMessage({ id: 'm9', content: 'All set.' })])
        renderPanel(travelPlan({ complete: true }))

        expect(await within(log()).findByText('All set.')).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: 'Continue planning' })).not.toBeInTheDocument()
    })

    it('has nothing to carry on with while a question is open', async () => {
        mockTranscript([])
        renderPanel(planAsking(ASK_DATES))

        await screen.findByRole('form', { name: 'When are you going?' })
        expect(screen.queryByRole('button', { name: 'Continue planning' })).not.toBeInTheDocument()
    })

    it('says so when the form is stale', async () => {
        mockTranscript([])
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            jsonResponse({ detail: "That question isn't the one being asked any more." }, 409),
        )
        const { user } = renderPanel(planAsking(ASK_DESTINATION))

        await user.type(await screen.findByRole('textbox', { name: 'Destination' }), 'Lisbon')
        await user.click(screen.getByRole('button', { name: 'Continue' }))

        expect(await screen.findByRole('alert')).toHaveTextContent(
            "That question isn't the one being asked any more.",
        )
    })

    it('keeps a failure that could not be stored on screen', async () => {
        mockTranscript([])
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            sseResponse([{ type: 'error', detail: 'Could not reach Ollama', message: null }]),
        )
        const { user } = renderPanel(planAsking(ASK_DESTINATION))

        await user.type(await screen.findByRole('textbox', { name: 'Message' }), 'Lisbon')
        await user.click(screen.getByRole('button', { name: 'Send message' }))

        expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach Ollama')
    })
})
