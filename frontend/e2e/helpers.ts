import { expect, type Page } from '@playwright/test'

// Shared steps for the full-stack E2E specs. The API runs with LLM_PROVIDER=fake (see
// playwright.config.ts), whose scripted replies these specs rely on.

// ----- constants -----

export const PASSWORD = 'correct horse battery'

/** Canned replies from backend/src/features/chat/offline.py. */
export const OFFLINE_ADDED_REPLY = "Done! It's on your itinerary."
export const OFFLINE_REDIRECT_REPLY = "That's outside what I can help with"

// ----- steps -----

/** A fresh email per call, so parallel tests (and reruns) never collide. */
export function uniqueEmail(prefix = 'traveler'): string {
    return `${prefix}-${String(Date.now())}-${Math.random().toString(36).slice(2, 8)}@example.com`
}

/** Register through the UI and wait for the workspace. Returns the email used. */
export async function registerNewUser(page: Page, name = 'Ada'): Promise<string> {
    const email = uniqueEmail()
    await page.goto('/register')
    await page.getByLabel('Name (optional)').fill(name)
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: 'Create account' }).click()

    await expect(page.getByTestId('current-user')).toHaveText(name)
    return email
}

/** Create a trip from the sidebar and give it `title` (New trip opens an inline rename). */
export async function createTrip(page: Page, title: string): Promise<void> {
    await page.getByRole('button', { name: 'New trip' }).click()
    const nameInput = page.getByRole('textbox', { name: 'Trip name' })
    await nameInput.fill(title)
    await nameInput.press('Enter')

    await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible()
    await expect(page).toHaveURL(/\/plans\/[0-9a-f-]{36}$/)
}

/** Type a chat message and send it. */
export async function sendChatMessage(page: Page, text: string): Promise<void> {
    await page.getByRole('textbox', { name: 'Message' }).fill(text)
    await page.getByRole('button', { name: 'Send message' }).click()
}
