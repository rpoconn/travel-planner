import { expect, test, type Page } from '@playwright/test'

import { createTrip, registerNewUser, sendChatMessage } from './helpers.ts'

// Planning a trip with the planner driving. The API runs offline, where a scripted policy
// plays the model (backend/src/features/planning/agent/offline.py): it works on the first
// thing the trip still needs, and reads free text from a fixed phrase table, so typed answers
// here use its phrases.

/** The planner's fixed answer to a question when offline (planning/agent/offline.py). */
const OFFLINE_ANSWER = "I'm running offline (LLM_PROVIDER=fake)"

// Every test starts with a new user (home: Philadelphia, so the origin is known) and a trip.
test.beforeEach(async ({ page }) => {
    await registerNewUser(page)
    await createTrip(page, 'Spring break')
})

const conversation = (page: Page) => page.getByRole('log', { name: 'Conversation' })
const question = (page: Page, prompt: string) => page.getByRole('form', { name: prompt })
const choices = (page: Page, prompt: string) => page.getByRole('region', { name: prompt })
/** What the trip still needs, across the top of the page. */
const needs = (page: Page) => page.getByRole('region', { name: 'What the trip still needs' })
/** The card for an edit that says `text` (a trip can have several such cards). */
const updated = (page: Page, text: string) =>
    page.getByRole('region', { name: 'Trip updated' }).filter({ hasText: text })

// ----- steps -----

/** Basics by form and by the offline fake's phrases: Lisbon, a long weekend, two adults. */
async function answerBasics(page: Page) {
    const where = question(page, 'Where are you going?')
    await where.getByRole('textbox', { name: 'Destination' }).fill('Lisbon')
    await where.getByRole('button', { name: 'Continue' }).click()
    await expect(question(page, 'When are you going?')).toBeVisible()
    await sendChatMessage(page, 'a long weekend next month')
    const who = question(page, "Who's coming?")
    await who.getByRole('spinbutton', { name: 'Adults' }).fill('2')
    await who.getByRole('button', { name: 'Continue' }).click()
    // Home is the origin already, so the basics are in: the flights are next.
    await expect(question(page, 'How do you like to fly?')).toBeVisible()
}

/** Say the trip has no flights, in the planner's words for it. */
async function drive(page: Page) {
    await sendChatMessage(page, "We're driving")
    await expect(updated(page, 'The trip has no flights')).toBeVisible()
    await expect(question(page, 'What kind of place do you want to stay in?')).toBeVisible()
}

/** A hotel for every night: the preferences in words, then the first of the list. */
async function bookStay(page: Page) {
    await sendChatMessage(page, 'a midrange hotel')
    await choices(page, 'Pick a place to stay.')
        .getByRole('button', { name: /^Pick / })
        .first()
        .click()
    await expect(question(page, 'What do you like to do?')).toBeVisible()
}

/** Flights both ways and a hotel for every night, taking the first of each list. */
async function bookFlightsAndStay(page: Page) {
    const how = question(page, 'How do you like to fly?')
    await how.getByRole('radio', { name: 'Economy' }).click()
    await how.getByRole('button', { name: 'Continue' }).click()
    await choices(page, 'Pick your flight out.')
        .getByRole('button', { name: /^Pick / })
        .first()
        .click()
    await choices(page, 'Pick your flight home.')
        .getByRole('button', { name: /^Pick / })
        .first()
        .click()
    await expect(question(page, 'What kind of place do you want to stay in?')).toBeVisible()
    await bookStay(page)
}

/** One thing to do, so the trip has an activity: the interests in words, then a pick. */
async function pickAnActivity(page: Page) {
    await sendChatMessage(page, 'museums and food')
    const todo = choices(page, "Pick the things you'd like to do.")
    await todo.getByRole('button', { name: 'Add Example Art Museum' }).click()
    await todo.getByRole('button', { name: 'Add 1 pick' }).click()
    await expect(choices(page, 'How do you want to get there?')).toBeVisible()
}

// ----- answering -----

test('answers the basics with forms and free text, then is asked about flights', async ({
    page,
}) => {
    // A new trip opens on its first question. A form answer.
    const where = question(page, 'Where are you going?')
    await where.getByRole('textbox', { name: 'Destination' }).fill('Lisbon')
    await where.getByRole('button', { name: 'Continue' }).click()
    await expect(conversation(page)).toContainText('Lisbon')
    await expect(needs(page)).toContainText('things left')

    // Free text is the planner's to read: it writes the dates, and says so.
    await expect(question(page, 'When are you going?')).toBeVisible()
    await sendChatMessage(page, 'next month for a week')
    await expect(updated(page, 'Dates:')).toBeVisible()

    const who = question(page, "Who's coming?")
    await who.getByRole('spinbutton', { name: 'Adults' }).fill('2')
    await who.getByRole('button', { name: 'Continue' }).click()
    await expect(conversation(page)).toContainText('2 adults')

    // Home is the origin already. What is left is listed, and the first of it asked about.
    await expect(question(page, 'How do you like to fly?')).toBeVisible()
    await expect(needs(page)).toContainText('4 things left')
    await expect(needs(page)).toContainText('A flight out')
})

test('answers a question, then puts the open one back', async ({ page }) => {
    await expect(question(page, 'Where are you going?')).toBeVisible()

    await sendChatMessage(page, 'Where is warm in March?')

    await expect(conversation(page)).toContainText(OFFLINE_ANSWER)
    // The question is asked again under the answer, and the plan is unchanged.
    await expect(question(page, 'Where are you going?')).toBeVisible()
})

test('works on the problem picked with Fix this', async ({ page }) => {
    await answerBasics(page)

    await needs(page).getByRole('button', { name: 'Fix this: at least one activity' }).click()

    await expect(question(page, 'What do you like to do?')).toBeVisible()
})

// ----- searching and picking -----

test('picks the flights out and home by list and by free text', async ({ page }) => {
    await answerBasics(page)

    // Answering the preferences leads to the search (the offline fake's canned flights).
    const how = question(page, 'How do you like to fly?')
    await how.getByRole('radio', { name: 'Economy' }).click()
    await how.getByRole('button', { name: 'Continue' }).click()
    const out = choices(page, 'Pick your flight out.')
    await expect(out.getByRole('article')).toHaveCount(2)

    // More options searches again; the canned search has nothing new, so the list stays.
    await out.getByRole('button', { name: 'More options' }).click()
    await expect(conversation(page)).toContainText('Those are all the options I found.')
    await expect(out.getByRole('article')).toHaveCount(2)

    // Free text picks from the list ("the second one"), and the flights home are listed.
    await sendChatMessage(page, 'the second one')
    await expect(conversation(page)).toContainText('Got it: Example Air EX 102.')
    await choices(page, 'Pick your flight home.')
        .getByRole('button', { name: 'Pick Example Air EX 102' })
        .click()

    // Both flights are in, so the planner turns to the place to stay.
    await expect(question(page, 'What kind of place do you want to stay in?')).toBeVisible()
    await expect(needs(page)).not.toContainText('A flight')
})

test('plans without flights when told so, and finds a place to stay', async ({ page }) => {
    await answerBasics(page)

    // The words are the approval: the trip has no flights from here on.
    await drive(page)
    await expect(needs(page)).not.toContainText('A flight')

    // Free text answers the lodging preferences, and the search runs for all three nights.
    await sendChatMessage(page, 'a midrange hotel')
    await expect(updated(page, 'Lodging preferences: Midrange hotel')).toBeVisible()
    const stays = choices(page, 'Pick a place to stay.')
    await expect(stays).toContainText('3 nights')
    await expect(stays.getByRole('article')).toHaveCount(2)

    // Picking one covers every night, so the planner turns to things to do.
    await stays.getByRole('button', { name: 'Pick Example Riverside Hotel' }).click()
    await expect(question(page, 'What do you like to do?')).toBeVisible()
})

test('plans a whole trip, home to home, with forms and picks', async ({ page }) => {
    await answerBasics(page)
    await bookFlightsAndStay(page)

    // Activities: free text for the interests, then several picks sent together.
    await sendChatMessage(page, 'museums and food')
    const todo = choices(page, "Pick the things you'd like to do.")
    await todo.getByRole('button', { name: 'Add Example Art Museum' }).click()
    await todo.getByRole('button', { name: 'Add Example Bistro' }).click()
    await todo.getByRole('button', { name: 'Add 2 picks' }).click()

    // Transfers: one list per gap, each saying which gap it's for, until none are left.
    const ways = choices(page, 'How do you want to get there?')
    await expect(ways).toContainText('From Home to')
    const complete = needs(page).getByText('Trip complete')
    for (let gap = 0; gap < 12 && !(await complete.isVisible()); gap += 1) {
        const before = await ways.textContent()
        await ways
            .getByRole('button', { name: /^Pick / })
            .first()
            .click()
        // The next gap's list (or the end of the trip) replaces this one once the pick is in.
        await expect
            .poll(async () => (await complete.isVisible()) || (await ways.textContent()) !== before)
            .toBe(true)
    }

    // Nothing is left: the planner says so, and has nothing more to ask.
    await expect(complete).toBeVisible()
    await expect(needs(page).getByRole('button', { name: /^Fix this/ })).toHaveCount(0)
    await expect(conversation(page)).toContainText('Your trip has everything it needs.')
    await expect(page.getByRole('button', { name: 'Continue planning' })).toHaveCount(0)
})

test('drives the own car on a trip without flights', async ({ page }) => {
    await answerBasics(page)
    await drive(page)
    await bookStay(page)
    await pickAnActivity(page)

    // Leaving home with no flights, the own car comes first.
    const ways = choices(page, 'How do you want to get there?')
    await expect(ways).toContainText('From Home to')
    const ownCar = ways.getByRole('button', { name: /^Pick Your car to / })
    const before = await ways.textContent()
    await ownCar.first().click()

    // The car is on the trip now, so the next gap leads with it too, and offers no rental.
    await expect.poll(async () => ways.textContent()).not.toBe(before)
    await expect(ways.getByRole('button', { name: /^Pick / }).first()).toHaveAccessibleName(
        /^Pick Your car to /,
    )
    await expect(ways).not.toContainText('Rental car')
})

// ----- changing the trip -----

test('removes the hotel by typing, and is offered another', async ({ page }) => {
    await answerBasics(page)
    await bookFlightsAndStay(page)
    await pickAnActivity(page)

    // An edit while the transfer choices are open: the words are the approval.
    await sendChatMessage(page, 'remove the hotel')

    await expect(updated(page, 'Removed “')).toBeVisible()
    // Every night is uncovered again, which comes before the rides: with the preferences
    // already answered, the planner searches for another place at once.
    await expect(needs(page)).toContainText('A place to stay')
    await expect(choices(page, 'Pick a place to stay.')).toBeVisible()
})

test('removes the hotel from the itinerary, says what the trip needs, and finds another', async ({
    page,
}) => {
    await answerBasics(page)
    await bookFlightsAndStay(page)

    // The stay's bar on the timeline opens its details, with a button to remove it.
    const itinerary = page.getByRole('region', { name: 'Itinerary' })
    await itinerary.getByRole('button', { name: /, \d+ nights?$/ }).click()
    await page.getByRole('button', { name: 'Remove from trip' }).click()
    // The confirm opens over the item's drawer, which is a dialog too.
    await page
        .getByRole('dialog', { name: /^Remove “/ })
        .getByRole('button', { name: 'Remove' })
        .click()

    // The card says what the trip needs now and how to fix it; then the planner, with the
    // preferences already answered, searches for another place at once.
    const card = updated(page, 'Removed “')
    await expect(card).toContainText('The trip needs a place to stay')
    await expect(card).toContainText('Pick another place to stay')
    await expect(choices(page, 'Pick a place to stay.')).toBeVisible()
})

// ----- suggestions -----

test("approves a change the planner suggests, and it's made", async ({ page }) => {
    await answerBasics(page)
    await drive(page)
    await bookStay(page)
    await pickAnActivity(page)
    const itinerary = page.getByRole('region', { name: 'Itinerary' })
    await expect(itinerary).toContainText('Example Art Museum')

    // The planner's own idea is a card to approve, not a change: the museum is still there.
    await sendChatMessage(page, 'What would you drop?')
    const card = page.getByRole('region', { name: 'Suggested changes' })
    await expect(card).toContainText('Remove “Example Art Museum”')
    await expect(itinerary).toContainText('Example Art Museum')

    await card.getByRole('button', { name: 'Approve' }).click()

    await expect(updated(page, 'Removed “Example Art Museum”')).toBeVisible()
    await expect(card).toHaveCount(0)
    await expect(itinerary).not.toContainText('Example Art Museum')
})

test('rejects a change the planner suggests, and nothing changes', async ({ page }) => {
    await answerBasics(page)
    await drive(page)
    await bookStay(page)
    await pickAnActivity(page)

    await sendChatMessage(page, 'What would you drop?')
    const card = page.getByRole('region', { name: 'Suggested changes' })
    await card.getByRole('button', { name: 'Reject' }).click()

    await expect(card).toHaveCount(0)
    await expect(conversation(page)).toContainText('No, leave it as it is.')
    await expect(page.getByRole('region', { name: 'Itinerary' })).toContainText(
        'Example Art Museum',
    )
    // The choices the suggestion was made over are still the ones to pick from.
    await expect(choices(page, 'How do you want to get there?')).toBeVisible()
})
