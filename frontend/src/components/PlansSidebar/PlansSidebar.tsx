import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'

import {
    createPlanMutation,
    deletePlanMutation,
    getPlanQueryKey,
    listPlansOptions,
    listPlansQueryKey,
    updatePlanMutation,
} from '@/api/@tanstack/react-query.gen'
import type { PlanSummary } from '@/api/types.gen'
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { errorMessage } from '@/lib/apiError'
import { formatDateRange } from '@/lib/format'
import { cn } from '@/lib/utils'

// ----- types -----

interface PlansSidebarProps {
    selectedPlanId?: string | undefined
    onSelectPlan: (planId: string) => void
    onPlanDeleted: (planId: string) => void
}

function planSubtitle(plan: PlanSummary): string {
    const parts = [plan.destination?.name, formatDateRange(plan.start_date, plan.end_date)]
    return parts.filter(Boolean).join(' · ') || 'No dates yet'
}

export function PlansSidebar({ selectedPlanId, onSelectPlan, onPlanDeleted }: PlansSidebarProps) {
    // ----- state -----
    const queryClient = useQueryClient()
    const plansQuery = useQuery(listPlansOptions())

    const [editingId, setEditingId] = useState<string | null>(null)
    const [draftTitle, setDraftTitle] = useState('')
    const [pendingDelete, setPendingDelete] = useState<PlanSummary | null>(null)
    // Escape blurs the rename input to close it; this tells the blur handler not to save.
    const cancelRenameRef = useRef(false)

    // ----- mutations -----
    const refreshPlans = () => queryClient.invalidateQueries({ queryKey: listPlansQueryKey() })

    const createPlan = useMutation({
        ...createPlanMutation(),
        onSuccess: async (plan) => {
            await refreshPlans()
            onSelectPlan(plan.id)
            startRename(plan.id, plan.title)
        },
    })

    const renamePlan = useMutation({
        ...updatePlanMutation(),
        // Optimistically show the new title; the refetch in onSettled reconciles either way.
        onMutate: async ({ path, body }) => {
            await queryClient.cancelQueries({ queryKey: listPlansQueryKey() })
            queryClient.setQueryData<PlanSummary[]>(listPlansQueryKey(), (plans) =>
                plans?.map((plan) =>
                    plan.id === path.plan_id ? { ...plan, title: body.title ?? plan.title } : plan,
                ),
            )
        },
        onSuccess: (plan) => {
            queryClient.setQueryData(getPlanQueryKey({ path: { plan_id: plan.id } }), plan)
        },
        onSettled: refreshPlans,
    })

    const deletePlan = useMutation({
        ...deletePlanMutation(),
        onSuccess: async (_data, { path }) => {
            queryClient.removeQueries({ queryKey: getPlanQueryKey({ path }) })
            setPendingDelete(null)
            onPlanDeleted(path.plan_id)
            await refreshPlans()
        },
    })

    // ----- event handlers -----
    function startRename(planId: string, title: string) {
        cancelRenameRef.current = false
        setEditingId(planId)
        setDraftTitle(title)
    }

    function finishRename(plan: PlanSummary) {
        const title = draftTitle.trim()
        const cancelled = cancelRenameRef.current
        cancelRenameRef.current = false
        setEditingId(null)
        if (!cancelled && title && title !== plan.title) {
            renamePlan.mutate({ path: { plan_id: plan.id }, body: { title } })
        }
    }

    function confirmDelete() {
        if (pendingDelete) deletePlan.mutate({ path: { plan_id: pendingDelete.id } })
    }

    // ----- markup -----
    const mutationError = createPlan.error ?? renamePlan.error

    return (
        <nav aria-label="Trips" className="flex h-full flex-col">
            <div className="flex items-center justify-between px-4 py-3">
                <h2 className="text-sm font-semibold">Your trips</h2>
                <Button
                    size="sm"
                    variant="outline"
                    disabled={createPlan.isPending}
                    onClick={() => {
                        createPlan.mutate({ body: { title: 'Untitled trip' } })
                    }}
                >
                    <Plus aria-hidden />
                    New trip
                </Button>
            </div>

            {mutationError && (
                <p role="alert" className="text-destructive px-4 pb-2 text-xs">
                    {errorMessage(mutationError)}
                </p>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
                {plansQuery.isPending && (
                    <p className="text-muted-foreground px-2 text-sm">Loading trips…</p>
                )}
                {plansQuery.isError && (
                    <p role="alert" className="text-destructive px-2 text-sm">
                        {errorMessage(plansQuery.error, "Couldn't load your trips")}
                    </p>
                )}
                {plansQuery.data?.length === 0 && (
                    <p className="text-muted-foreground px-2 text-sm">
                        No trips yet. Create one to start planning.
                    </p>
                )}

                <ul className="flex flex-col gap-0.5">
                    {plansQuery.data?.map((plan) => {
                        const selected = plan.id === selectedPlanId
                        return (
                            <li key={plan.id} className="group relative">
                                {editingId === plan.id ? (
                                    <Input
                                        aria-label="Trip name"
                                        autoFocus
                                        maxLength={200}
                                        value={draftTitle}
                                        className="h-auto py-2"
                                        onChange={(event) => {
                                            setDraftTitle(event.target.value)
                                        }}
                                        onFocus={(event) => {
                                            event.currentTarget.select()
                                        }}
                                        onKeyDown={(event) => {
                                            // Both keys funnel through onBlur so saving happens
                                            // in exactly one place.
                                            if (event.key === 'Escape') {
                                                cancelRenameRef.current = true
                                                event.currentTarget.blur()
                                            } else if (event.key === 'Enter') {
                                                event.currentTarget.blur()
                                            }
                                        }}
                                        onBlur={() => {
                                            finishRename(plan)
                                        }}
                                    />
                                ) : (
                                    <>
                                        <button
                                            type="button"
                                            aria-current={selected ? 'page' : undefined}
                                            className={cn(
                                                'hover:bg-muted w-full rounded-lg px-3 py-2 pr-16 text-left transition-colors',
                                                selected && 'bg-muted',
                                            )}
                                            onClick={() => {
                                                onSelectPlan(plan.id)
                                            }}
                                        >
                                            <span className="block truncate text-sm font-medium">
                                                {plan.title}
                                            </span>
                                            <span className="text-muted-foreground block truncate text-xs">
                                                {planSubtitle(plan)}
                                            </span>
                                        </button>
                                        <div className="absolute top-1/2 right-1 flex -translate-y-1/2 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                                            <Button
                                                size="icon-xs"
                                                variant="ghost"
                                                aria-label={`Rename ${plan.title}`}
                                                onClick={() => {
                                                    startRename(plan.id, plan.title)
                                                }}
                                            >
                                                <Pencil aria-hidden />
                                            </Button>
                                            <Button
                                                size="icon-xs"
                                                variant="ghost"
                                                aria-label={`Delete ${plan.title}`}
                                                onClick={() => {
                                                    setPendingDelete(plan)
                                                }}
                                            >
                                                <Trash2 aria-hidden />
                                            </Button>
                                        </div>
                                    </>
                                )}
                            </li>
                        )
                    })}
                </ul>
            </div>

            <AlertDialog
                open={pendingDelete !== null}
                onOpenChange={(open) => {
                    if (!open) setPendingDelete(null)
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete “{pendingDelete?.title}”?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This removes the trip and everything planned in it. This can’t be
                            undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {deletePlan.isError && (
                        <p role="alert" className="text-destructive text-sm">
                            {errorMessage(deletePlan.error)}
                        </p>
                    )}
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            variant="destructive"
                            disabled={deletePlan.isPending}
                            onClick={(event) => {
                                // Keep the dialog open until the delete succeeds.
                                event.preventDefault()
                                confirmDelete()
                            }}
                        >
                            Delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </nav>
    )
}
