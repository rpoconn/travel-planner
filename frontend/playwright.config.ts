import { defineConfig, devices } from '@playwright/test'

// E2E runs the real stack on its own ports, so it never talks to (or reuses) a dev server:
// the API in offline mode (fake LLM + search, fresh SQLite file; backend/scripts/e2e_server.py)
// and Vite pointed at it through VITE_API_URL.
const UI_PORT = 5174
const API_PORT = 8765
const UI_URL = `http://localhost:${String(UI_PORT)}`
const API_URL = `http://localhost:${String(API_PORT)}`

export default defineConfig({
    testDir: './e2e',
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    reporter: 'list',
    use: {
        baseURL: UI_URL,
        trace: 'on-first-retry',
    },
    projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
    webServer: [
        {
            command: `uv --directory ../backend run python scripts/e2e_server.py --port ${String(API_PORT)} --cors-origin ${UI_URL}`,
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
    ],
})
