import { describe, expect, it } from 'vitest'

import { stageStatuses, travelPlan } from '@/test/fixtures'

import {
    currentStatus,
    STAGE_META,
    STAGES,
    stageIndex,
    stageState,
    stageStatus,
    suggestionsFor,
} from './stages'

describe('stages', () => {
    it('lists every stage once, in workflow order, with a label', () => {
        expect(STAGES).toEqual(['basics', 'flights', 'lodging', 'activities', 'ground', 'review'])
        expect(STAGES.map(stageIndex)).toEqual([0, 1, 2, 3, 4, 5])
        for (const stage of STAGES) expect(STAGE_META[stage].label).not.toBe('')
    })

    it('marks earlier stages done, the current one current, and later ones locked', () => {
        const plan = travelPlan({ stage: 'lodging' })

        expect(STAGES.map((stage) => stageState(plan, stage))).toEqual([
            'done',
            'done',
            'current',
            'locked',
            'locked',
            'locked',
        ])
    })

    it('marks a passed stage incomplete when its checklist no longer holds', () => {
        const plan = travelPlan({
            stage: 'activities',
            stages: stageStatuses({ lodging: ['a place to stay the night of May 2'] }),
        })

        expect(stageState(plan, 'lodging')).toBe('incomplete')
        expect(stageState(plan, 'flights')).toBe('done')
    })

    it('reads the current stage checklist from the plan', () => {
        const plan = travelPlan({
            stage: 'flights',
            stages: stageStatuses({ flights: ['a flight home'] }),
        })

        expect(currentStatus(plan)).toEqual({
            stage: 'flights',
            ready: false,
            missing: ['a flight home'],
        })
    })

    it('treats a stage the server did not report as not ready', () => {
        expect(stageStatus(travelPlan({ stages: [] }), 'ground')).toEqual({
            stage: 'ground',
            ready: false,
            missing: [],
        })
    })

    it('suggests messages for the current stage, naming the destination', () => {
        expect(suggestionsFor(travelPlan({ stage: 'flights' }))[0]).toBe('Find flights to Tokyo')
        expect(suggestionsFor(travelPlan({ stage: 'lodging' }))).toContain('Add a hotel')
        expect(suggestionsFor(travelPlan({ stage: 'ground' }))).toContain('Add transfers')
        expect(suggestionsFor(travelPlan({ destination: null }))[0]).toBe(
            'I want to spend a week in Tokyo in May',
        )
    })
})
