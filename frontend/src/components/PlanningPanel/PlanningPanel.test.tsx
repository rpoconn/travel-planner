import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { getPlanQueryKey } from '@/api/@tanstack/react-query.gen'
import * as sdk from '@/api/sdk.gen'
import type { PlanningMessage, TravelPlan } from '@/api/types.gen'
import {
    ASK_DATES,
    ASK_DESTINATION,
    OFFER_FLIGHTS_OUT,
    issue,
    PLAN_ID,
    planAsking,
    planAssessment,
    planChoosing,
    planningMessage,
    sdkOk,
    travelPlan,
} from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'
import { controlledSseResponse, jsonResponse, sseResponse } from '@/test/sse'

import { PlanningPanel } from './PlanningPanel'

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
            planningMessage({ id: 'm3', role: 'system', kind: 'stage', content: 'Basics done.' }),
            planningMessage({ id: 'm4', kind: 'nudge', content: 'Which dates?' }),
            ASKED_DATES,
        ])

        renderPanel(planAsking(ASK_DATES))

        expect(await within(log()).findByText('Lisbon, Portugal')).toBeInTheDocument()
        expect(screen.getByRole('separator', { name: 'Basics done.' })).toBeInTheDocument()
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
            body: { refresh: true },
        })
    })

    it('lists what is left to fix at review', async () => {
        mockTranscript([])
        const missing = issue({ stage: 'lodging', message: 'a place to stay the night of May 3' })
        renderPanel(
            travelPlan({
                stage: 'review',
                assessment: planAssessment({ stage: 'review', issues: [missing] }),
            }),
        )

        const left = await screen.findByRole('region', { name: 'Before you go' })
        expect(left).toHaveTextContent('Missing a place to stay the night of May 3')
        expect(within(left).getByRole('button', { name: /^Go to Lodging/ })).toBeEnabled()
    })

    it('shows no fix list before review', async () => {
        mockTranscript([])
        renderPanel(planAsking(ASK_DATES))

        await screen.findByRole('form', { name: 'When are you going?' })
        expect(screen.queryByRole('region', { name: 'Before you go' })).not.toBeInTheDocument()
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
        expect(await sentRequest(fetchSpy)).toEqual({
            url: `http://api.test/plans/${PLAN_ID}/planning/chat`,
            body: { content: 'Lisbon please' },
        })
        stream.push({ type: 'status', text: 'Thinking…' })
        expect(await within(log()).findByText('Thinking…')).toBeInTheDocument()
        stream.push({ type: 'done', plan: planAsking(ASK_DESTINATION) })
        stream.close()
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
        })
    })

    it('drives the planner on with the stage bar', async () => {
        mockTranscript([])
        const fetchSpy = vi
            .spyOn(globalThis, 'fetch')
            .mockResolvedValue(sseResponse([{ type: 'done', plan: travelPlan() }]))
        const plan = travelPlan({
            assessment: planAssessment({ next_action: { type: 'advance_stage', to: 'flights' } }),
        })
        const { user } = renderPanel(plan)

        await user.click(await screen.findByRole('button', { name: 'Continue to Flights' }))

        const request = fetchSpy.mock.calls[0]?.[0] as Request
        expect(request.url).toBe(`http://api.test/plans/${PLAN_ID}/planning/continue`)
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
