import { expect, type Page } from '@playwright/test'

// Shared steps for the full-stack E2E specs. The API runs with LLM_PROVIDER=fake (see
// playwright.config.ts), whose scripted replies these specs rely on.

// ----- constants -----

export const PASSWORD = 'correct horse battery'

/** Canned replies (prefixes) from backend/src/features/chat/offline.py and pipeline.py. */
export const OFFLINE_PROPOSED_REPLY = "Here's my suggestion"
export const OFFLINE_ADDED_REPLY = "Done! It's on your itinerary."
export const DENIED_REPLY = 'No problem — nothing was changed.'
export const OFFLINE_REDIRECT_REPLY = "That's outside what I can help with"
export const OFFLINE_FIX_REPLY = "Here's what I'd change to fix that"

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

/** The newest proposal card in the conversation. */
export function latestProposal(page: Page) {
    return page
        .getByRole('log', { name: 'Conversation' })
        .getByRole('region', { name: 'Proposed changes' })
        .last()
}

/** Approve the newest proposal card and wait for the assistant's confirmation. */
export async function approveProposal(page: Page): Promise<void> {
    const card = latestProposal(page)
    await card.getByRole('button', { name: 'Approve' }).click()
    await expect(card.getByText('Approved', { exact: true })).toBeVisible()
}

/** "add <what>" in offline mode: send it, then approve the proposed activity. */
export async function addViaChat(page: Page, what: string): Promise<void> {
    await sendChatMessage(page, `Add ${what}`)
    await expect(latestProposal(page).getByRole('button', { name: 'Approve' })).toBeEnabled()
    await approveProposal(page)
}
