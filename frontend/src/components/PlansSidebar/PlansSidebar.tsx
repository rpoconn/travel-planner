import { ActionIcon, Button, TextInput } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { IconPencil, IconPlus, IconTrash } from '@tabler/icons-react'
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
import { ConfirmDialog } from '@/components/ConfirmDialog/ConfirmDialog'
import { errorMessage } from '@/lib/apiError'
import { formatTripWhereWhen } from '@/lib/format'

// ----- types -----

interface PlansSidebarProps {
    selectedPlanId?: string | undefined
    onSelectPlan: (planId: string) => void
    onPlanDeleted: (planId: string) => void
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
                    variant="default"
                    disabled={createPlan.isPending}
                    leftSection={<IconPlus aria-hidden />}
                    onClick={() => {
                        createPlan.mutate({ body: { title: 'Untitled trip' } })
                    }}
                >
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
                                    <TextInput
                                        aria-label="Trip name"
                                        autoFocus
                                        maxLength={200}
                                        value={draftTitle}
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
                                            className={clsx(
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
                                                {formatTripWhereWhen(plan) ?? 'No dates yet'}
                                            </span>
                                        </button>
                                        <div className="absolute top-1/2 right-1 flex -translate-y-1/2 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                                            <ActionIcon
                                                size="sm"
                                                variant="subtle"
                                                aria-label={`Rename ${plan.title}`}
                                                onClick={() => {
                                                    startRename(plan.id, plan.title)
                                                }}
                                            >
                                                <IconPencil aria-hidden />
                                            </ActionIcon>
                                            <ActionIcon
                                                size="sm"
                                                variant="subtle"
                                                aria-label={`Delete ${plan.title}`}
                                                onClick={() => {
                                                    setPendingDelete(plan)
                                                }}
                                            >
                                                <IconTrash aria-hidden />
                                            </ActionIcon>
                                        </div>
                                    </>
                                )}
                            </li>
                        )
                    })}
                </ul>
            </div>

            {/* Stays open until the delete succeeds; the mutation's onSuccess closes it. */}
            <ConfirmDialog
                opened={pendingDelete !== null}
                title={`Delete “${pendingDelete?.title ?? ''}”?`}
                confirmLabel="Delete"
                loading={deletePlan.isPending}
                error={deletePlan.isError ? errorMessage(deletePlan.error) : undefined}
                onConfirm={confirmDelete}
                onCancel={() => {
                    setPendingDelete(null)
                }}
            >
                This removes the trip and everything planned in it. This can’t be undone.
            </ConfirmDialog>
        </nav>
    )
}
