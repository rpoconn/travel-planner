import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CalendarRange, LogOut, MessagesSquare, Plane } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'

import { getPlanOptions } from '@/api/@tanstack/react-query.gen'
import { ChatPanel, type ChatPanelHandle } from '@/components/ChatPanel/ChatPanel'
import { ItineraryTimeline } from '@/components/ItineraryTimeline/ItineraryTimeline'
import { PlansSidebar } from '@/components/PlansSidebar/PlansSidebar'
import { ThemeToggle } from '@/components/ThemeToggle/ThemeToggle'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { clearSession, useAuthSession } from '@/lib/auth'
import { formatDateRange } from '@/lib/format'

// Three-pane shell: trips sidebar | chat | itinerary timeline. The chat and timeline are
// keyed by plan id so switching trips starts them fresh (and aborts a streaming reply).

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
    // The timeline's "Fix it" buttons send through the chat panel (a sibling), and are
    // disabled while it streams a reply.
    const chatRef = useRef<ChatPanelHandle>(null)
    const [chatBusy, setChatBusy] = useState(false)

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
            <ChatPanel
                key={planQuery.data.id}
                ref={chatRef}
                plan={planQuery.data}
                onBusyChange={setChatBusy}
            />
        )
    }

    function renderItineraryPane() {
        if (planId === undefined || !plan) {
            return (
                <EmptyPane icon={<CalendarRange aria-hidden />} title="Itinerary">
                    Select a trip to see its itinerary.
                </EmptyPane>
            )
        }
        return (
            <ItineraryTimeline
                key={planId}
                planId={planId}
                onRequestFix={(warning) => {
                    chatRef.current?.sendFix(warning)
                }}
                fixDisabled={chatBusy}
            />
        )
    }

    return (
        <div className="flex h-screen flex-col">
            <header className="from-primary/10 via-background to-terracotta/12 flex h-14 shrink-0 items-center justify-between border-b bg-linear-to-r px-4">
                <span className="font-heading flex items-center gap-2 text-xl">
                    <span className="bg-primary text-primary-foreground flex size-8 items-center justify-center rounded-full">
                        <Plane className="size-4" aria-hidden />
                    </span>
                    Travel Planner
                </span>
                <div className="flex items-center gap-3 text-sm">
                    <ThemeToggle />
                    <span className="text-muted-foreground" data-testid="current-user">
                        {session && (session.user.display_name || session.user.email)}
                    </span>
                    <Button size="sm" variant="ghost" onClick={handleSignOut}>
                        <LogOut aria-hidden />
                        Sign out
                    </Button>
                </div>
            </header>

            <div className="grid min-h-0 flex-1 grid-cols-[17rem_minmax(22rem,1fr)_minmax(0,1.5fr)]">
                <aside className="bg-sidebar min-h-0 border-r">
                    <PlansSidebar
                        selectedPlanId={planId}
                        onSelectPlan={handleSelectPlan}
                        onPlanDeleted={handlePlanDeleted}
                    />
                </aside>

                <section aria-label="Chat" className="flex min-h-0 flex-col border-r">
                    {plan && (
                        <div className="border-b px-6 py-3">
                            <h1 className="font-heading truncate text-xl">{plan.title}</h1>
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

                <section aria-label="Itinerary" className="bg-muted/40 min-h-0 min-w-0">
                    {renderItineraryPane()}
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
