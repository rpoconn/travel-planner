import { defineConfig, devices } from '@playwright/test'

// E2E runs the real stack on its own ports, so it never talks to (or reuses) a dev server:
// the API in offline mode (fake LLM + search, fresh SQLite file; backend/scripts/e2e_server.py)
// and Vite pointed at it through VITE_API_URL.
// While both planning flows exist, a second Vite server runs the rules-driven planning flow
// (VITE_PLANNING_ENGINE=true) for `planning.spec.ts`; every other spec uses the chat flow.
const UI_PORT = 5174
const PLANNING_UI_PORT = 5175
const API_PORT = 8765
const UI_URL = `http://localhost:${String(UI_PORT)}`
const PLANNING_UI_URL = `http://localhost:${String(PLANNING_UI_PORT)}`
const API_URL = `http://localhost:${String(API_PORT)}`
const PLANNING_SPEC = /planning\.spec\.ts/

export default defineConfig({
    testDir: './e2e',
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    reporter: 'list',
    use: {
        trace: 'on-first-retry',
    },
    projects: [
        {
            name: 'chromium',
            testIgnore: PLANNING_SPEC,
            use: { ...devices['Desktop Chrome'], baseURL: UI_URL },
        },
        {
            name: 'planning',
            testMatch: PLANNING_SPEC,
            use: { ...devices['Desktop Chrome'], baseURL: PLANNING_UI_URL },
        },
    ],
    webServer: [
        {
            command: `uv --directory ../backend run python scripts/e2e_server.py --port ${String(API_PORT)} --cors-origin ${UI_URL} --cors-origin ${PLANNING_UI_URL}`,
            url: `${API_URL}/health`,
            reuseExistingServer: false,
            timeout: 60_000,
        },
        {
            command: `npm run dev -- --port ${String(UI_PORT)} --strictPort`,
            url: UI_URL,
            reuseExistingServer: false,
            env: { VITE_API_URL: API_URL },
        },
        {
            command: `npm run dev -- --port ${String(PLANNING_UI_PORT)} --strictPort`,
            url: PLANNING_UI_URL,
            reuseExistingServer: false,
            env: { VITE_API_URL: API_URL, VITE_PLANNING_ENGINE: 'true' },
        },
    ],
})
