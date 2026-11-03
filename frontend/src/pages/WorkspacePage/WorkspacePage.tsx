import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CalendarRange, LogOut, MessagesSquare, Plane } from 'lucide-react'
import { useRef, useState } from 'react'

import { getPlanOptions } from '@/api/@tanstack/react-query.gen'
import { ChatPanel, type ChatPanelHandle } from '@/components/ChatPanel/ChatPanel'
import { EmptyPane } from '@/components/EmptyPane/EmptyPane'
import { HomeSetup } from '@/components/HomeSetup/HomeSetup'
import { ItineraryTimeline } from '@/components/ItineraryTimeline/ItineraryTimeline'
import { PlansSidebar } from '@/components/PlansSidebar/PlansSidebar'
import { SplitPane } from '@/components/SplitPane/SplitPane'
import { StageStepper } from '@/components/StageStepper/StageStepper'
import { ThemeToggle } from '@/components/ThemeToggle/ThemeToggle'
import { TimelineWarnings } from '@/components/TimelineWarnings/TimelineWarnings'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { clearSession, useAuthSession } from '@/lib/auth'
import { formatDateRange } from '@/lib/format'

// Shell: a full-height trips sidebar on the left; on the right the itinerary timeline stacked
// above the chat. The chat and timeline are keyed by plan id so switching trips starts them
// fresh (and aborts a streaming reply). The stage steps span the top of the page and lock
// while a reply streams, since the assistant's tools depend on the stage the turn started in.
// The Back / Next controls live inline in the chat transcript (see ChatPanel). The itinerary's
// "things to check" sit at the right end of the stage bar, leaving the timeline its room.

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
    // The warnings' "Fix it" buttons send through the chat panel (a sibling), and are
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
                    <HomeSetup />
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

            {/* The planning steps span the whole page, with the itinerary's warnings at the end. */}
            {plan && (
                <div className="flex items-center gap-4 border-b pr-4">
                    <StageStepper plan={plan} disabled={chatBusy} />
                    <TimelineWarnings
                        key={plan.id}
                        planId={plan.id}
                        onRequestFix={(warning) => {
                            chatRef.current?.sendFix(warning)
                        }}
                        fixDisabled={chatBusy}
                    />
                </div>
            )}

            <div className="grid min-h-0 flex-1 grid-cols-[17rem_minmax(0,1fr)]">
                <aside className="bg-sidebar min-h-0 border-r">
                    <PlansSidebar
                        selectedPlanId={planId}
                        onSelectPlan={handleSelectPlan}
                        onPlanDeleted={handlePlanDeleted}
                    />
                </aside>

                {/* Right column: itinerary on top, chat below, with a draggable divider. */}
                <SplitPane
                    storageKey="workspace-split"
                    label="Resize itinerary and chat"
                    top={
                        <section aria-label="Itinerary" className="bg-muted/40 h-full min-w-0">
                            {planId === undefined || !plan ? (
                                <EmptyPane icon={<CalendarRange aria-hidden />} title="Itinerary">
                                    Select a trip to see its itinerary.
                                </EmptyPane>
                            ) : (
                                <ItineraryTimeline key={planId} planId={planId} />
                            )}
                        </section>
                    }
                    bottom={
                        <section aria-label="Chat" className="flex h-full min-w-0 flex-col">
                            {plan && (
                                <div className="flex items-baseline gap-3 border-b px-6 py-2">
                                    <h1 className="font-heading truncate text-xl">{plan.title}</h1>
                                    <p className="text-muted-foreground shrink-0 text-xs">
                                        {[
                                            plan.destination?.name,
                                            formatDateRange(plan.start_date, plan.end_date),
                                        ]
                                            .filter(Boolean)
                                            .join(' · ') || 'Tell the assistant where and when'}
                                    </p>
                                </div>
                            )}
                            <div className="min-h-0 flex-1">
                                {planId === undefined ? (
                                    <EmptyPane
                                        icon={<MessagesSquare aria-hidden />}
                                        title="Pick a trip to start"
                                    >
                                        Choose a trip on the left, or create a new one.
                                    </EmptyPane>
                                ) : planQuery.isPending ? (
                                    <p className="text-muted-foreground p-6 text-sm">
                                        Loading trip…
                                    </p>
                                ) : planQuery.isError ? (
                                    <p role="alert" className="text-destructive p-6 text-sm">
                                        {errorMessage(planQuery.error, "Couldn't load this trip")}
                                    </p>
                                ) : (
                                    <ChatPanel
                                        key={planQuery.data.id}
                                        ref={chatRef}
                                        plan={planQuery.data}
                                        onBusyChange={setChatBusy}
                                    />
                                )}
                            </div>
                        </section>
                    }
                />
            </div>
        </div>
    )
}
