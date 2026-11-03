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
export const OFFLINE_STAGE_DONE_REPLY = 'That completes this stage: press Next'
export const OFFLINE_WITHDRAWN_REPLY = "Sorry, scratch that: a dinner isn't somewhere to stay"

/** The stepper's labels, in workflow order (frontend/src/lib/stages.ts). */
export const STAGE_LABELS = {
    basics: 'Basics',
    flights: 'Flights',
    lodging: 'Lodging',
    activities: 'Activities',
    ground: 'Transfers',
    review: 'Review',
} as const

export type Stage = keyof typeof STAGE_LABELS
const STAGE_ORDER = Object.keys(STAGE_LABELS) as Stage[]

// ----- steps -----

/** A fresh email per call, so parallel tests (and reruns) never collide. */
export function uniqueEmail(prefix = 'traveler'): string {
    return `${prefix}-${String(Date.now())}-${Math.random().toString(36).slice(2, 8)}@example.com`
}

/**
 * Register through the UI and wait for the workspace. Returns the email used. A new user is
 * asked where home is; unless `home` is null that's answered (Philadelphia, PHL), so new
 * trips start with an origin and the sheet is out of the way.
 */
export async function registerNewUser(
    page: Page,
    name = 'Ada',
    { home = 'Philadelphia' }: { home?: string | null } = {},
): Promise<string> {
    const email = uniqueEmail()
    await page.goto('/register')
    await page.getByLabel('Name (optional)').fill(name)
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: 'Create account' }).click()

    await expect(page.getByTestId('current-user')).toHaveText(name)
    if (home !== null) await setHome(page, home)
    return email
}

/** Save `city` (and its airport) as home: through the sheet that opens for a new user, or
 * the header button. */
export async function setHome(page: Page, city: string, iata = 'PHL'): Promise<void> {
    const sheet = page.getByRole('dialog', { name: 'Where is home?' })
    // The sheet opens by itself on the first render without a home; otherwise use the button.
    if (!(await sheet.isVisible()))
        await page.getByRole('button', { name: /^(Set home|Home:)/ }).click()
    await sheet.getByLabel('City or town').fill(city)
    await sheet.getByLabel(/Nearest airport code/).fill(iata)
    await sheet.getByRole('button', { name: 'Save home' }).click()

    await expect(sheet).toBeHidden()
    await expect(page.getByRole('button', { name: `Home: ${city}` })).toBeVisible()
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

// ----- planning stages -----

/** The step list across the top of the page. */
export function stepper(page: Page) {
    return page.getByRole('navigation', { name: 'Planning stages' })
}

/** The Back / Skip flights / Next buttons and "Missing" text, at the end of the chat
 * transcript (hidden while a reply streams). */
export function stageControls(page: Page) {
    return page.getByRole('region', { name: 'Stage controls' })
}

/** Wait until the stepper shows `stage` as the current one. */
export async function expectStage(page: Page, stage: Stage): Promise<void> {
    // Playwright's getByRole has no `current` option, so match the attribute directly.
    await expect(stepper(page).locator('li[aria-current="step"]')).toContainText(
        STAGE_LABELS[stage],
    )
}

/** Press Next and wait for the following stage. (The click waits for the reply to finish:
 * the stage controls only appear in the transcript once the assistant is done.) */
export async function nextStage(page: Page, to: Stage): Promise<void> {
    await stageControls(page).getByRole('button', { name: 'Next' }).click()
    await expectStage(page, to)
}

/** Press the stage's next-step button ("Find a hotel", "Add transfers", …), which asks the
 * assistant to do the stage's work, and wait for the card it proposes. */
export async function askNextStep(page: Page, label: string): Promise<void> {
    await stageControls(page).getByRole('button', { name: label }).click()
    await expect(latestProposal(page).getByRole('button', { name: 'Approve' })).toBeEnabled()
}

/** From the flights stage, plan the trip without flying. */
export async function skipFlights(page: Page): Promise<void> {
    await stageControls(page).getByRole('button', { name: 'Skip flights' }).click()
    await expectStage(page, 'lodging')
}

/** Send an offline command and approve what it proposes. */
export async function proposeAndApprove(page: Page, command: string): Promise<void> {
    await sendChatMessage(page, command)
    await expect(latestProposal(page).getByRole('button', { name: 'Approve' })).toBeEnabled()
    await approveProposal(page)
}

/**
 * Walk a fresh trip to `target` with the offline commands: set up a Paris trip, skip
 * flights, add a hotel for every night, add a visit to the Louvre, then a taxi per gap.
 */
export async function reachStage(page: Page, target: Stage): Promise<void> {
    const steps: Record<Exclude<Stage, 'review'>, () => Promise<void>> = {
        basics: async () => {
            await proposeAndApprove(page, 'Set up the trip')
            await nextStage(page, 'flights')
        },
        flights: () => skipFlights(page),
        lodging: async () => {
            await proposeAndApprove(page, 'Add a hotel')
            await nextStage(page, 'activities')
        },
        activities: async () => {
            await addViaChat(page, 'a visit to the Louvre')
            await nextStage(page, 'ground')
        },
        ground: async () => {
            await proposeAndApprove(page, 'Add transfers')
            await nextStage(page, 'review')
        },
    }
    for (const stage of STAGE_ORDER.slice(0, STAGE_ORDER.indexOf(target))) {
        if (stage !== 'review') await steps[stage]()
    }
    await expectStage(page, target)
}
