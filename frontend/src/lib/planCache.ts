import type { QueryClient } from '@tanstack/react-query'

import {
    getPlanQueryKey,
    getTimelineQueryKey,
    listPlansQueryKey,
} from '@/api/@tanstack/react-query.gen'
import type { TravelPlan } from '@/api/types.gen'

/**
 * Apply a `plan_updated` snapshot from the chat stream: the plan is written straight into the
 * cache (instant header update), while the timeline (derived server-side) and the sidebar list
 * are refetched. Repeated invalidations cancel the previous in-flight refetch, so a burst of
 * tool calls ends with one fresh timeline.
 */
export function applyPlanSnapshot(queryClient: QueryClient, plan: TravelPlan): void {
    const path = { plan_id: plan.id }
    queryClient.setQueryData(getPlanQueryKey({ path }), plan)
    void queryClient.invalidateQueries({ queryKey: getTimelineQueryKey({ path }) })
    void queryClient.invalidateQueries({ queryKey: listPlansQueryKey() })
}
