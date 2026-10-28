import { createFileRoute } from '@tanstack/react-router'

// Matches `/` (no plan selected). Rendering is done by the `_workspace` layout.
export const Route = createFileRoute('/_authed/_workspace/')()
