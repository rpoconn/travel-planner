// Feature switch between the chat flow and the rules-driven planning flow, while both exist.
// Set `VITE_PLANNING_ENGINE=true` (e.g. in `.env.local`) to use the planning flow. Read on
// every call rather than once at import, so tests can switch it with `vi.stubEnv`.

/** Whether the workspace shows the planning flow instead of the chat flow. */
export function planningEngineEnabled(): boolean {
    return (import.meta.env.VITE_PLANNING_ENGINE as string | undefined) === 'true'
}
