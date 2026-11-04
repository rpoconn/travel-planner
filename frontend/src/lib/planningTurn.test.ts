import { describe, expect, it } from 'vitest'

import { planningMessage, travelPlan } from '@/test/fixtures'

import { applyPlanningEvent, startPlanningTurn } from './planningTurn'

const USER = planningMessage({ id: 'u1', role: 'user', content: 'Is May warm?' })
const REPLY = planningMessage({ id: 'a1', content: 'Yes, around 22°C.' })

describe('planning turn reducer', () => {
    it('shows the free text until the stored copy arrives', () => {
        let turn = startPlanningTurn('Is May warm?')
        expect(turn.pendingText).toBe('Is May warm?')

        turn = applyPlanningEvent(turn, { type: 'message', message: USER })

        expect(turn.pendingText).toBeNull()
        expect(turn.messages).toEqual([USER])
    })

    it('streams an answer, then swaps it for the stored message', () => {
        let turn = startPlanningTurn()
        turn = applyPlanningEvent(turn, { type: 'token', text: 'Yes, ' })
        turn = applyPlanningEvent(turn, { type: 'token', text: 'around 22°C.' })
        expect(turn.streamingText).toBe('Yes, around 22°C.')

        turn = applyPlanningEvent(turn, { type: 'message', message: REPLY })

        expect(turn.streamingText).toBe('')
        expect(turn.messages).toEqual([REPLY])
    })

    it('shows what the planner is doing until the next message', () => {
        let turn = applyPlanningEvent(startPlanningTurn(), {
            type: 'status',
            text: 'Find flights out…',
        })
        expect(turn.activity).toBe('Find flights out…')

        turn = applyPlanningEvent(turn, { type: 'message', message: REPLY })

        expect(turn.activity).toBeNull()
    })

    it('ends with done, ignoring plan updates', () => {
        let turn = applyPlanningEvent(startPlanningTurn(), {
            type: 'plan_updated',
            plan: travelPlan(),
        })
        turn = applyPlanningEvent(turn, { type: 'done', plan: travelPlan() })

        expect(turn.status).toBe('done')
        expect(turn.messages).toEqual([])
    })

    it('keeps a failure and whether it was stored', () => {
        const notice = planningMessage({ id: 'e1', role: 'system', kind: 'error', content: 'Down' })

        const stored = applyPlanningEvent(startPlanningTurn(), {
            type: 'error',
            detail: 'Down',
            message: notice,
        })
        const lost = applyPlanningEvent(startPlanningTurn(), {
            type: 'error',
            detail: 'Down',
            message: null,
        })

        expect(stored).toMatchObject({ status: 'error', error: 'Down', errorStored: true })
        expect(stored.messages).toEqual([notice])
        expect(lost).toMatchObject({ status: 'error', errorStored: false, messages: [] })
    })
})
