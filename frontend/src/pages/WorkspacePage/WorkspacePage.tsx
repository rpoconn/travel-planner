import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import {
    CalendarRange,
    LogOut,
    MessagesSquare,
    PanelLeftClose,
    PanelLeftOpen,
    Plane,
} from 'lucide-react'
import { useReducer, useRef, useState } from 'react'

import { getPlanOptions } from '@/api/@tanstack/react-query.gen'
import { EmptyPane } from '@/components/EmptyPane/EmptyPane'
import { HomeSetup } from '@/components/HomeSetup/HomeSetup'
import { IssuesPanel } from '@/components/IssuesPanel/IssuesPanel'
import { ItineraryTimeline } from '@/components/ItineraryTimeline/ItineraryTimeline'
import { PlanningPanel, type PlanningPanelHandle } from '@/components/PlanningPanel/PlanningPanel'
import { PlansSidebar } from '@/components/PlansSidebar/PlansSidebar'
import { SplitPane } from '@/components/SplitPane/SplitPane'
import { StageStepper } from '@/components/StageStepper/StageStepper'
import { ThemeToggle } from '@/components/ThemeToggle/ThemeToggle'
import { TripSummary } from '@/components/TripSummary/TripSummary'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/apiError'
import { clearSession, useAuthSession } from '@/lib/auth'
import { cn } from '@/lib/utils'

import { initialTripsPanel, tripsPanelReducer, tripsToggleLabel } from './WorkspacePageService'

// Shell: a full-height trips sidebar on the left; on the right the itinerary timeline stacked
// above the planning panel. The sidebar hides once the user starts working in a trip (see
// WorkspacePageService); the button at the left of the header shows or hides it. The header
// also names the selected trip (TripSummary), so the two panes need no title bars. Both panes
// are keyed by plan id so switching trips starts them fresh
// (and aborts a streaming turn). The stage steps span the top of the page and lock while a
// turn streams, since the turn acts on the stage it started in. The planner's issues ("things
// to check", with "Go to" a stage) sit at the right end of the stage bar, leaving the
// timeline its room. An item's Remove button in the timeline runs as a turn in the planning
// panel, so the planner's answer (what the trip needs now) shows in the chat.

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
    // The stage steps and issues lock while the planning panel streams a turn.
    const [chatBusy, setChatBusy] = useState(false)
    const panelRef = useRef<PlanningPanelHandle>(null)
    const [tripsPanel, dispatchTripsPanel] = useReducer(
        tripsPanelReducer,
        planId !== undefined,
        initialTripsPanel,
    )

    // ----- event handlers -----
    function handleSelectPlan(id: string) {
        dispatchTripsPanel('trip_selected')
        void navigate({ to: '/plans/$planId', params: { planId: id } })
    }

    function handlePlanDeleted(id: string) {
        if (id === planId) void navigate({ to: '/' })
    }

    /** A click or key press in the trip itself: the user is planning, so the trips panel hides. */
    function handleTripInteraction() {
        if (planId !== undefined) dispatchTripsPanel('trip_interaction')
    }

    function handleSignOut() {
        // The authed layout notices the session ended and redirects to /login.
        clearSession()
    }

    // ----- markup -----
    return (
        <div className="flex h-screen flex-col">
            <header className="from-primary/10 via-background to-terracotta/12 flex h-12 shrink-0 items-center justify-between gap-4 border-b bg-linear-to-r px-4">
                <div className="flex min-w-0 items-center gap-2">
                    <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={tripsToggleLabel(tripsPanel.open)}
                        aria-expanded={tripsPanel.open}
                        aria-controls="trips-panel"
                        onClick={() => {
                            dispatchTripsPanel('toggled')
                        }}
                    >
                        {tripsPanel.open ? (
                            <PanelLeftClose aria-hidden />
                        ) : (
                            <PanelLeftOpen aria-hidden />
                        )}
                    </Button>
                    <span className="font-heading flex shrink-0 items-center gap-2 text-lg">
                        <span className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-full">
                            <Plane className="size-3.5" aria-hidden />
                        </span>
                        {/* On a narrow page the app's name makes way for the trip's. */}
                        <span className={cn(plan && 'max-md:hidden')}>Travel Planner</span>
                    </span>
                    {plan && (
                        <>
                            <span className="bg-border mx-1.5 h-5 w-px shrink-0" aria-hidden />
                            <TripSummary plan={plan} />
                        </>
                    )}
                </div>
                <div className="flex shrink-0 items-center gap-3 text-sm">
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

            {/* The planning steps span the whole page, with the planner's issues at the end. */}
            {plan && (
                <div
                    className="flex items-center gap-4 border-b pr-4"
                    onClickCapture={handleTripInteraction}
                    onKeyDownCapture={handleTripInteraction}
                >
                    <StageStepper plan={plan} disabled={chatBusy} />
                    <IssuesPanel key={plan.id} plan={plan} disabled={chatBusy} />
                </div>
            )}

            <div
                className={cn(
                    'grid min-h-0 flex-1 transition-[grid-template-columns] duration-200 motion-reduce:transition-none',
                    tripsPanel.open
                        ? 'grid-cols-[17rem_minmax(0,1fr)]'
                        : 'grid-cols-[0rem_minmax(0,1fr)]',
                )}
            >
                {/* Hidden, the panel is a zero-width column: `inert` takes it out of the tab
                    order and the accessibility tree, and the inner fixed width keeps its content
                    from reflowing while the column animates. */}
                <aside
                    id="trips-panel"
                    inert={!tripsPanel.open}
                    className={cn(
                        'bg-sidebar min-h-0 overflow-hidden',
                        tripsPanel.open && 'border-r',
                    )}
                >
                    <div className="h-full w-[17rem]">
                        <PlansSidebar
                            selectedPlanId={planId}
                            onSelectPlan={handleSelectPlan}
                            onPlanDeleted={handlePlanDeleted}
                        />
                    </div>
                </aside>

                {/* Right column: itinerary on top, chat below, with a draggable divider. The
                    capture handlers see every click and key press in the trip, including those
                    in its popovers and sheets (React events bubble through portals). */}
                <div
                    className="h-full min-h-0 min-w-0"
                    onClickCapture={handleTripInteraction}
                    onKeyDownCapture={handleTripInteraction}
                >
                    <SplitPane
                        storageKey="workspace-split"
                        label="Resize itinerary and chat"
                        top={
                            <section aria-label="Itinerary" className="bg-muted/40 h-full min-w-0">
                                {planId === undefined || !plan ? (
                                    <EmptyPane
                                        icon={<CalendarRange aria-hidden />}
                                        title="Itinerary"
                                    >
                                        Select a trip to see its itinerary.
                                    </EmptyPane>
                                ) : (
                                    <ItineraryTimeline
                                        key={planId}
                                        planId={planId}
                                        onRemoveItem={(item) =>
                                            panelRef.current?.removeItem(item.id)
                                        }
                                        removeDisabled={chatBusy}
                                    />
                                )}
                            </section>
                        }
                        bottom={
                            <section aria-label="Chat" className="flex h-full min-w-0 flex-col">
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
                                            {errorMessage(
                                                planQuery.error,
                                                "Couldn't load this trip",
                                            )}
                                        </p>
                                    ) : (
                                        <PlanningPanel
                                            ref={panelRef}
                                            key={planQuery.data.id}
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
        </div>
    )
}
