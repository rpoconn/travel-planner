import { createFileRoute } from '@tanstack/react-router'

// Matches `/plans/$planId`. Rendering is done by the `_workspace` layout, which reads the param.
export const Route = createFileRoute('/_authed/_workspace/plans/$planId')()
