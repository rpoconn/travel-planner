import { describe, expect, it } from 'vitest'

import {
    ASK_DATES,
    ASK_DESTINATION,
    OFFER_FLIGHTS_OUT,
    planAsking,
    planningMessage,
    SUGGESTION,
    travelPlan,
} from '@/test/fixtures'

import {
    canContinue,
    composerPlaceholder,
    mergeTranscript,
    visibleMessages,
} from './PlanningPanelService'

// ----- setup -----

const ANSWER = planningMessage({ id: 'm1', role: 'user', kind: 'input', content: 'Lisbon' })
const ASKED_DATES = planningMessage({
    id: 'm2',
    kind: 'expected_input',
    content: 'When are you going?',
    payload: ASK_DATES,
})

// ----- tests -----

describe('mergeTranscript', () => {
    it("appends the turn's messages the history doesn't hold yet", () => {
        expect(mergeTranscript([ANSWER], [ANSWER, ASKED_DATES])).toEqual([ANSWER, ASKED_DATES])
    })
})

describe('visibleMessages', () => {
    it('leaves out the last question when the form asks it', () => {
        expect(visibleMessages([ANSWER, ASKED_DATES], ASK_DATES)).toEqual([ANSWER])
    })

    it('keeps a question that is not the pending one, or not the last message', () => {
        const reply = planningMessage({ id: 'm3', content: 'May is lovely.' })

        expect(visibleMessages([ANSWER, ASKED_DATES], ASK_DESTINATION)).toHaveLength(2)
        expect(visibleMessages([ASKED_DATES, reply], ASK_DATES)).toHaveLength(2)
        expect(visibleMessages([ASKED_DATES], null)).toHaveLength(1)
    })
})

describe('visibleMessages with choices', () => {
    const shown = planningMessage({
        id: 'm4',
        kind: 'choices',
        content: 'Pick your flight out.',
        payload: OFFER_FLIGHTS_OUT,
    })

    it('leaves out the last choices when the list shows the same offer', () => {
        expect(visibleMessages([ANSWER, shown], OFFER_FLIGHTS_OUT)).toEqual([ANSWER])
    })

    it('keeps choices from another offer', () => {
        const newer = { ...OFFER_FLIGHTS_OUT, offer_id: 'bbbbbbbb-0000-0000-0000-000000000009' }

        expect(visibleMessages([ANSWER, shown], newer)).toHaveLength(2)
    })
})

describe('visibleMessages with a suggestion', () => {
    const reason = planningMessage({ id: 'm5', content: SUGGESTION.prompt })

    it("leaves out the suggestion's reason when its card shows it", () => {
        expect(visibleMessages([ANSWER, reason], SUGGESTION)).toEqual([ANSWER])
    })

    it('keeps any other last message', () => {
        const other = planningMessage({ id: 'm6', content: 'May is lovely.' })
        const typed = planningMessage({ id: 'm7', role: 'user', content: SUGGESTION.prompt })

        expect(visibleMessages([ANSWER, other], SUGGESTION)).toHaveLength(2)
        expect(visibleMessages([ANSWER, typed], SUGGESTION)).toHaveLength(2)
    })
})

describe('canContinue', () => {
    it('offers to carry on only when nothing is asked and the trip needs something', () => {
        expect(canContinue(travelPlan())).toBe(true)
        expect(canContinue(planAsking(ASK_DATES))).toBe(false)
        expect(canContinue(travelPlan({ complete: true }))).toBe(false)
    })
})

describe('composerPlaceholder', () => {
    it('repeats the pending question', () => {
        expect(composerPlaceholder(ASK_DATES)).toBe(
            'When are you going? Answer here or in the form, or ask for a change.',
        )
        expect(composerPlaceholder(OFFER_FLIGHTS_OUT)).toBe(
            'Pick one above, say which ("the second one"), or ask for a change.',
        )
        expect(composerPlaceholder(SUGGESTION)).toBe(
            "Approve or reject above, or say what you'd rather do.",
        )
        expect(composerPlaceholder(null)).toBe('Ask about your trip, or ask for a change…')
    })
})
