import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CalendarRange, LogOut, MessagesSquare, Plane } from 'lucide-react'
import type { ReactNode } from 'react'

import { getPlanOptions } from '@/api/@tanstack/react-query.gen'
import { PlansSidebar } from '@/components/PlansSidebar/PlansSidebar'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { clearSession, useAuthSession } from '@/lib/auth'
import { formatDateRange } from '@/lib/format'

// Three-pane shell: trips sidebar | chat | itinerary timeline.
// Chat (Phase 3/5) and the timeline (Phase 5) are placeholders for now.

interface WorkspacePageProps {
    planId?: string
}

export function WorkspacePage({ planId }: WorkspacePageProps) {
    // ----- state -----
    const navigate = useNavigate()
    const session = useAuthSession()
    const planQuery = useQuery({
        ...getPlanOptions({ path: { plan_id: planId ?? '' } }),
        enabled: planId !== undefined,
    })
    const plan = planId === undefined ? undefined : planQuery.data

    // ----- event handlers -----
    function handleSelectPlan(id: string) {
        void navigate({ to: '/plans/$planId', params: { planId: id } })
    }

    function handlePlanDeleted(id: string) {
        if (id === planId) void navigate({ to: '/' })
    }

    function handleSignOut() {
        // The authed layout notices the session ended and redirects to /login.
        clearSession()
    }

    // ----- markup -----
    function renderChatPane() {
        if (planId === undefined) {
            return (
                <EmptyPane icon={<MessagesSquare aria-hidden />} title="Pick a trip to start">
                    Choose a trip on the left, or create a new one.
                </EmptyPane>
            )
        }
        if (planQuery.isPending) {
            return <p className="text-muted-foreground p-6 text-sm">Loading trip…</p>
        }
        if (planQuery.isError) {
            return (
                <p role="alert" className="text-destructive p-6 text-sm">
                    {errorMessage(planQuery.error, "Couldn't load this trip")}
                </p>
            )
        }
        return (
            <EmptyPane icon={<MessagesSquare aria-hidden />} title="Chat coming soon">
                Your travel assistant will live here.
            </EmptyPane>
        )
    }

    return (
        <div className="flex h-screen flex-col">
            <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
                <span className="flex items-center gap-2 font-semibold">
                    <Plane className="size-5" aria-hidden />
                    Travel Planner
                </span>
                <div className="flex items-center gap-3 text-sm">
                    <span className="text-muted-foreground" data-testid="current-user">
                        {session && (session.user.display_name || session.user.email)}
                    </span>
                    <Button size="sm" variant="ghost" onClick={handleSignOut}>
                        <LogOut aria-hidden />
                        Sign out
                    </Button>
                </div>
            </header>

            <div className="grid min-h-0 flex-1 grid-cols-[18rem_minmax(0,1fr)_minmax(0,1.25fr)]">
                <aside className="min-h-0 border-r">
                    <PlansSidebar
                        selectedPlanId={planId}
                        onSelectPlan={handleSelectPlan}
                        onPlanDeleted={handlePlanDeleted}
                    />
                </aside>

                <section aria-label="Chat" className="flex min-h-0 flex-col border-r">
                    {plan && (
                        <div className="border-b px-6 py-3">
                            <h1 className="truncate text-lg font-semibold">{plan.title}</h1>
                            <p className="text-muted-foreground text-xs">
                                {[
                                    plan.destination?.name,
                                    formatDateRange(plan.start_date, plan.end_date),
                                ]
                                    .filter(Boolean)
                                    .join(' · ') || 'Tell the assistant where and when'}
                            </p>
                        </div>
                    )}
                    <div className="min-h-0 flex-1">{renderChatPane()}</div>
                </section>

                <section aria-label="Itinerary" className="bg-muted/30 min-h-0">
                    <EmptyPane icon={<CalendarRange aria-hidden />} title="Itinerary">
                        {plan
                            ? 'Flights, stays, and activities will appear here as a timeline.'
                            : 'Select a trip to see its itinerary.'}
                    </EmptyPane>
                </section>
            </div>
        </div>
    )
}

// ----- subcomponents -----

interface EmptyPaneProps {
    icon: ReactNode
    title: string
    children: ReactNode
}

function EmptyPane({ icon, title, children }: EmptyPaneProps) {
    return (
        <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 p-6 text-center [&_svg]:size-8">
            {icon}
            <p className="text-foreground font-medium">{title}</p>
            <p className="max-w-xs text-sm">{children}</p>
        </div>
    )
}
