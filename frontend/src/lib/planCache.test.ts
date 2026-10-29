import { describe, expect, it, vi } from 'vitest'

import {
    getPlanQueryKey,
    getTimelineQueryKey,
    listPlansQueryKey,
} from '@/api/@tanstack/react-query.gen'
import { PLAN_ID, travelPlan } from '@/test/fixtures'
import { createTestQueryClient } from '@/test/render'

import { applyPlanSnapshot } from './planCache'

describe('applyPlanSnapshot', () => {
    it('writes the plan into the cache and refreshes the timeline and plan list', () => {
        const queryClient = createTestQueryClient()
        const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
        const plan = travelPlan({ version: 5, title: 'Tokyo, updated' })

        applyPlanSnapshot(queryClient, plan)

        const path = { plan_id: PLAN_ID }
        expect(queryClient.getQueryData(getPlanQueryKey({ path }))).toEqual(plan)
        expect(invalidate).toHaveBeenCalledWith({ queryKey: getTimelineQueryKey({ path }) })
        expect(invalidate).toHaveBeenCalledWith({ queryKey: listPlansQueryKey() })
    })
})
