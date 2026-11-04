import { describe, expect, it } from 'vitest'

import { ASK_DESTINATION, planAssessment, travelPlan } from '@/test/fixtures'

import { continueButton, tripComplete } from './StageBarService'

describe('continueButton', () => {
    it('runs a pending search under its label', () => {
        const plan = travelPlan({
            stage: 'flights',
            assessment: planAssessment({
                next_action: {
                    type: 'run_search',
                    slot: 'flight_outbound',
                    stage: 'flights',
                    label: 'Find flights out',
                    nights: null,
                    gap: null,
                },
            }),
        })

        expect(continueButton(plan)).toEqual({ label: 'Find flights out', kind: 'search' })
    })

    it('moves on from a finished stage the user came back to', () => {
        const plan = travelPlan({
            assessment: planAssessment({ next_action: { type: 'advance_stage', to: 'flights' } }),
        })

        expect(continueButton(plan)).toEqual({ label: 'Continue to Flights', kind: 'advance' })
    })

    it('has nothing to add while a form is pending or the trip is done', () => {
        const asking = travelPlan({ assessment: planAssessment({ next_action: ASK_DESTINATION }) })

        expect(continueButton(asking)).toBeNull()
        expect(continueButton(travelPlan())).toBeNull()
    })
})

describe('tripComplete', () => {
    it('is true at review with no blocking issue', () => {
        expect(tripComplete(travelPlan({ stage: 'review' }))).toBe(true)
        expect(tripComplete(travelPlan({ stage: 'basics' }))).toBe(false)
    })

    it('is false while a stage still blocks the trip', () => {
        const plan = travelPlan({
            stage: 'review',
            assessment: planAssessment({
                stage: 'review',
                issues: [
                    {
                        code: 'missing_transfer',
                        stage: 'ground',
                        severity: 'blocking',
                        message: 'a way from the airport',
                        date: null,
                        item_ids: [],
                        gap: null,
                    },
                ],
            }),
        })

        expect(tripComplete(plan)).toBe(false)
    })
})
