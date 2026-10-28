import { createFileRoute, useParams } from '@tanstack/react-router'

import { WorkspacePage } from '@/pages/WorkspacePage/WorkspacePage'

// Pathless layout that owns the workspace shell. Keeping WorkspacePage here (instead of in
// the `/` and `/plans/$planId` leaf routes) means switching plans doesn't remount the
// sidebar and lose its state, e.g. the rename box opened right after creating a trip.
export const Route = createFileRoute('/_authed/_workspace')({
    component: WorkspaceLayout,
})

function WorkspaceLayout() {
    // Non-strict: planId is present only when a `/plans/$planId` child route is matched.
    const { planId } = useParams({ strict: false })
    return <WorkspacePage planId={planId} />
}
