import { describe, expect, it } from 'vitest'

import type { PlanStage } from '@/api/types.gen'
import { issue, planAssessment, travelPlan } from '@/test/fixtures'

import {
    fixStage,
    goToLabel,
    issueText,
    sortIssues,
    summaryLabel,
    visibleIssues,
} from './IssuesPanelService'

// ----- setup -----

const NO_HOTEL = issue({ stage: 'lodging', message: 'a place to stay the night of May 3' })
const NO_RIDE = issue({ stage: 'ground', message: 'a transfer from Home to SFO' })
const OVERLAP = issue({
    code: 'overlap',
    stage: 'activities',
    severity: 'warning',
    message: 'Senso-ji overlaps Tsukiji Market',
})

const NO_DATES = issue({ code: 'missing_dates', stage: 'basics', message: 'trip dates' })

function planAt(stage: PlanStage, issues = [NO_HOTEL, NO_RIDE, OVERLAP]) {
    return travelPlan({ stage, assessment: planAssessment({ stage, issues }) })
}

// ----- tests -----

describe('visibleIssues', () => {
    it("lists the current stage's own checklist, such as a ride removed since", () => {
        expect(visibleIssues(planAt('ground'))).toEqual([NO_HOTEL, NO_RIDE, OVERLAP])
    })

    it("leaves out the basics' checklist while the basics form is asking for it", () => {
        expect(visibleIssues(planAt('basics', [NO_DATES, OVERLAP]))).toEqual([OVERLAP])
    })

    it('lists a missing basic once the plan is past the basics', () => {
        expect(visibleIssues(planAt('flights', [NO_DATES]))).toEqual([NO_DATES])
    })

    it('lists everything at review', () => {
        expect(visibleIssues(planAt('review'))).toEqual([NO_HOTEL, NO_RIDE, OVERLAP])
    })
})

describe('sortIssues', () => {
    it('puts blocking issues first', () => {
        expect(sortIssues([OVERLAP, NO_HOTEL])).toEqual([NO_HOTEL, OVERLAP])
    })
})

describe('fixStage', () => {
    it('sends a blocking issue back to its earlier stage', () => {
        expect(fixStage(NO_HOTEL, planAt('review'))).toBe('lodging')
    })

    it("doesn't send warnings or the current stage anywhere", () => {
        expect(fixStage(OVERLAP, planAt('review'))).toBeNull()
        expect(fixStage(NO_RIDE, planAt('ground'))).toBeNull()
    })
})

describe('labels', () => {
    it('counts and names', () => {
        expect(summaryLabel(1)).toBe('1 thing to check')
        expect(summaryLabel(3)).toBe('3 things to check')
        expect(goToLabel('ground')).toBe('Go to Ground transport')
    })

    it('reads blocking issues as what is missing', () => {
        expect(issueText(NO_HOTEL)).toBe('Missing a place to stay the night of May 3')
        expect(issueText(OVERLAP)).toBe('Senso-ji overlaps Tsukiji Market')
    })
})
