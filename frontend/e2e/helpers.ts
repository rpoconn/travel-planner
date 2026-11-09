import { expect, type Page } from '@playwright/test'

// Shared steps for the full-stack E2E specs. The API runs offline (LLM_PROVIDER=fake and fake
// searches; see playwright.config.ts), whose canned replies these specs rely on.

// ----- constants -----

export const PASSWORD = 'correct horse battery'

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
    // The trips panel hides once a trip is worked in; bring it back for the New trip button.
    const showTrips = page.getByRole('button', { name: 'Show trips' })
    if (await showTrips.isVisible()) await showTrips.click()
    await page.getByRole('button', { name: 'New trip' }).click()
    const nameInput = page.getByRole('textbox', { name: 'Trip name' })
    await nameInput.fill(title)
    await nameInput.press('Enter')

    await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible()
    await expect(page).toHaveURL(/\/plans\/[0-9a-f-]{36}$/)
}

/** Type free text into the composer and send it. */
export async function sendChatMessage(page: Page, text: string): Promise<void> {
    await page.getByRole('textbox', { name: 'Message' }).fill(text)
    await page.getByRole('button', { name: 'Send message' }).click()
}
