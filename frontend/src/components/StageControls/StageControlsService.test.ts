import { describe, expect, it } from 'vitest'

import { stageStatuses, travelPlan } from '@/test/fixtures'

import { nextStep } from './StageControlsService'

// ----- tests -----

describe('nextStep', () => {
    it('sets up the trip while the basics are incomplete, naming what is missing', () => {
        const step = nextStep(
            travelPlan({
                stage: 'basics',
                destination: null,
                stages: stageStatuses({ basics: ['trip dates', 'a destination'] }),
            }),
        )

        expect(step).toEqual({
            label: 'Set up the trip',
            message: 'Set up the trip. Still needed: trip dates, a destination.',
            primary: true,
        })
    })

    it('finds both flights, or names the one leg still missing', () => {
        const both = nextStep(
            travelPlan({
                stage: 'flights',
                stages: stageStatuses({ flights: ['a flight out', 'a flight home'] }),
            }),
        )
        expect(both?.label).toBe('Find flights')
        expect(both?.message).toBe(
            'Find flights for the trip to Tokyo. Still needed: a flight out, a flight home.',
        )
        expect(both?.primary).toBe(true)

        const home = nextStep(
            travelPlan({ stage: 'flights', stages: stageStatuses({ flights: ['a flight home'] }) }),
        )
        expect(home?.label).toBe('Find a flight home')
    })

    it('starts the lodging and ground messages with the offline commands', () => {
        const lodging = nextStep(
            travelPlan({
                stage: 'lodging',
                stages: stageStatuses({ lodging: ['a place to stay the night of May 2'] }),
            }),
        )
        expect(lodging?.label).toBe('Find a hotel')
        expect(lodging?.message).toMatch(/^Add a hotel in Tokyo\./)

        const ground = nextStep(
            travelPlan({
                stage: 'ground',
                stages: stageStatuses({ ground: ['a transfer from Home to SFO'] }),
            }),
        )
        expect(ground?.label).toBe('Add transfers')
        expect(ground?.message).toMatch(/^Add transfers\b/)
    })

    it('has no step once a stage is done, so Next is the action', () => {
        for (const stage of ['basics', 'flights', 'lodging', 'ground'] as const) {
            expect(nextStep(travelPlan({ stage }))).toBeNull()
        }
    })

    it('always offers activities, as the main button only until there is one', () => {
        const none = nextStep(
            travelPlan({
                stage: 'activities',
                stages: stageStatuses({ activities: ['at least one activity'] }),
            }),
        )
        expect(none).toMatchObject({ label: 'Suggest activities', primary: true })
        expect(none?.message).toContain('in Tokyo')

        const some = nextStep(travelPlan({ stage: 'activities' }))
        expect(some).toMatchObject({ label: 'Suggest more', primary: false })
    })

    it('asks what is missing at review, or summarizes a complete trip', () => {
        const incomplete = nextStep(
            travelPlan({ stage: 'review', stages: stageStatuses({ flights: ['a flight out'] }) }),
        )
        expect(incomplete).toMatchObject({ label: "What's missing?", primary: true })
        expect(incomplete?.message).toContain('Still needed: the flights stage.')

        expect(nextStep(travelPlan({ stage: 'review' }))).toEqual({
            label: 'Summarize the trip',
            message: 'Summarize the trip.',
            primary: false,
        })
    })
})
