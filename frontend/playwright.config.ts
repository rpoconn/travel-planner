import { defineConfig, devices } from '@playwright/test'

const PORT = 5173

export default defineConfig({
    testDir: './e2e',
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    reporter: 'list',
    use: {
        baseURL: `http://localhost:${String(PORT)}`,
        trace: 'on-first-retry',
    },
    projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
    // Frontend only for now; Phase 6 adds the backend (LLM_PROVIDER=fake) as a second server.
    webServer: {
        command: `npm run dev -- --port ${String(PORT)} --strictPort`,
        url: `http://localhost:${String(PORT)}`,
        reuseExistingServer: !process.env.CI,
    },
})
