import { expect, test, type Page } from '@playwright/test'

import { createTrip, registerNewUser, sendChatMessage } from './helpers.ts'

// The rules-driven planning flow (run by the `planning` project, VITE_PLANNING_ENGINE=true).
// The API's offline fake reads free text from a fixed phrase table
// (backend/src/features/planning/offline.py), so typed answers here use its phrases.

/** The planner's fixed answer to a question when offline (planning/offline.py). */
const OFFLINE_ANSWER = "I'm running offline (LLM_PROVIDER=fake)"

// Every test starts with a new user (home: Philadelphia, so the origin is known) and a trip.
test.beforeEach(async ({ page }) => {
    await registerNewUser(page)
    await createTrip(page, 'Spring break')
})

const conversation = (page: Page) => page.getByRole('log', { name: 'Conversation' })
const question = (page: Page, prompt: string) => page.getByRole('form', { name: prompt })

test('answers the basics with forms and free text, then moves on to flights', async ({ page }) => {
    // A form answer.
    const where = question(page, 'Where are you going?')
    await where.getByRole('textbox', { name: 'Destination' }).fill('Lisbon')
    await where.getByRole('button', { name: 'Continue' }).click()
    await expect(conversation(page)).toContainText('Lisbon')

    // Free text, read into the dates slot.
    await expect(question(page, 'When are you going?')).toBeVisible()
    await sendChatMessage(page, 'next month for a week')
    await expect(conversation(page)).toContainText('Got it:')

    // Off-topic text while a question is pending gets the slot's nudge, and nothing changes.
    const who = question(page, "Who's coming?")
    await expect(who).toBeVisible()
    await sendChatMessage(page, 'Write me a poem about cheese')
    await expect(page.getByRole('complementary', { name: 'Back to the question' })).toContainText(
        'How many adults and children are travelling?',
    )
    await who.getByRole('spinbutton', { name: 'Adults' }).fill('2')
    await who.getByRole('button', { name: 'Continue' }).click()
    await expect(conversation(page)).toContainText('2 adults')

    // Home is the origin already, so the budget is next; skipping it finishes basics.
    const budget = question(page, 'Do you have a budget for the whole trip?')
    await budget.getByRole('button', { name: 'Skip' }).click()
    await expect(
        page.getByRole('separator', { name: 'Basics done. Next up: flights.' }),
    ).toBeVisible()
    await expect(question(page, 'How do you like to fly?')).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Planning stages' })).toContainText('Flights')
})

test('answers a question, then asks the pending one again', async ({ page }) => {
    await expect(question(page, 'Where are you going?')).toBeVisible()

    await sendChatMessage(page, 'Where is warm in March?')

    await expect(conversation(page)).toContainText(OFFLINE_ANSWER)
    // The question is asked again under the answer, and the plan is unchanged.
    await expect(question(page, 'Where are you going?')).toBeVisible()
})

test('picks the flights out and home by list and by free text, then moves on', async ({ page }) => {
    // Basics, quickly: the origin is the user's home already.
    const where = question(page, 'Where are you going?')
    await where.getByRole('textbox', { name: 'Destination' }).fill('Lisbon')
    await where.getByRole('button', { name: 'Continue' }).click()
    await expect(question(page, 'When are you going?')).toBeVisible()
    await sendChatMessage(page, 'next month for a week')
    const who = question(page, "Who's coming?")
    await who.getByRole('spinbutton', { name: 'Adults' }).fill('2')
    await who.getByRole('button', { name: 'Continue' }).click()
    await question(page, 'Do you have a budget for the whole trip?')
        .getByRole('button', { name: 'Skip' })
        .click()

    // Answering the preferences runs the search (the offline fake's canned flights).
    const how = question(page, 'How do you like to fly?')
    await how.getByRole('radio', { name: 'Economy' }).click()
    await how.getByRole('button', { name: 'Continue' }).click()
    const out = page.getByRole('region', { name: 'Pick your flight out.' })
    await expect(out.getByRole('article')).toHaveCount(2)

    // Free text picks from the list ("the second one"), and the flights home are listed.
    await sendChatMessage(page, 'the second one')
    await expect(conversation(page)).toContainText('Got it: Example Air EX 102.')
    const home = page.getByRole('region', { name: 'Pick your flight home.' })
    await home.getByRole('button', { name: 'Pick Example Air EX 102' }).click()

    // Both flights are in: the planner moves on to lodging by itself.
    await expect(
        page.getByRole('separator', { name: 'Flights done. Next up: lodging.' }),
    ).toBeVisible()
    await expect(question(page, 'What kind of place do you want to stay in?')).toBeVisible()
})

test('skips flights from the stage bar', async ({ page }) => {
    const where = question(page, 'Where are you going?')
    await where.getByRole('textbox', { name: 'Destination' }).fill('Lisbon')
    await where.getByRole('button', { name: 'Continue' }).click()
    await expect(question(page, 'When are you going?')).toBeVisible()
    await sendChatMessage(page, 'a long weekend next month')
    const who = question(page, "Who's coming?")
    await who.getByRole('spinbutton', { name: 'Adults' }).fill('1')
    await who.getByRole('button', { name: 'Continue' }).click()
    await question(page, 'Do you have a budget for the whole trip?')
        .getByRole('button', { name: 'Skip' })
        .click()
    await expect(question(page, 'How do you like to fly?')).toBeVisible()

    await page.getByRole('button', { name: 'Skip flights' }).click()

    await expect(question(page, 'What kind of place do you want to stay in?')).toBeVisible()
})

test('finds a place to stay for the trip, then moves on to activities', async ({ page }) => {
    // Basics, then skip flights: the stay is searched in the destination's own time.
    const where = question(page, 'Where are you going?')
    await where.getByRole('textbox', { name: 'Destination' }).fill('Lisbon')
    await where.getByRole('button', { name: 'Continue' }).click()
    await expect(question(page, 'When are you going?')).toBeVisible()
    await sendChatMessage(page, 'a long weekend next month')
    const who = question(page, "Who's coming?")
    await who.getByRole('spinbutton', { name: 'Adults' }).fill('2')
    await who.getByRole('button', { name: 'Continue' }).click()
    await question(page, 'Do you have a budget for the whole trip?')
        .getByRole('button', { name: 'Skip' })
        .click()
    await expect(question(page, 'How do you like to fly?')).toBeVisible()
    await page.getByRole('button', { name: 'Skip flights' }).click()

    // Free text answers the lodging preferences, and the search runs for all three nights.
    await expect(question(page, 'What kind of place do you want to stay in?')).toBeVisible()
    await sendChatMessage(page, 'a midrange hotel')
    await expect(conversation(page)).toContainText('Got it: Midrange hotel.')
    const stays = page.getByRole('region', { name: 'Pick a place to stay.' })
    await expect(stays).toContainText('3 nights')
    await expect(stays.getByRole('article')).toHaveCount(2)

    // Picking one covers every night, so the planner moves on by itself.
    await stays.getByRole('button', { name: 'Pick Example Riverside Hotel' }).click()
    await expect(
        page.getByRole('separator', { name: 'Lodging done. Next up: activities.' }),
    ).toBeVisible()
    await expect(question(page, 'What do you like to do?')).toBeVisible()
})
