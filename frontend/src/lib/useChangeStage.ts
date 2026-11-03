import { useMutation, useQueryClient } from '@tanstack/react-query'

import { changeStageMutation } from '@/api/@tanstack/react-query.gen'
import type { StageChange, TravelPlan } from '@/api/types.gen'
import { applyPlanSnapshot } from '@/lib/planCache'

/**
 * Moves a plan between planning stages. Shared by the step list and the stage controls, which
 * live in different parts of the page. The server checks readiness and answers 409 with the
 * reason when a move is refused; on success the returned plan replaces the cached one.
 */
export function useChangeStage(plan: TravelPlan) {
    const queryClient = useQueryClient()
    const mutation = useMutation({
        ...changeStageMutation(),
        onSuccess: (updated) => {
            applyPlanSnapshot(queryClient, updated)
        },
    })

    return {
        move: (body: StageChange) => {
            mutation.mutate({ path: { plan_id: plan.id }, body })
        },
        isPending: mutation.isPending,
        error: mutation.isError ? mutation.error : null,
    }
}
