import { expect, test, type Page } from '@playwright/test'

import {
    addViaChat,
    approveProposal,
    createTrip,
    DENIED_REPLY,
    expectStage,
    latestProposal,
    nextStage,
    OFFLINE_ADDED_REPLY,
    OFFLINE_FIX_REPLY,
    OFFLINE_PROPOSED_REPLY,
    OFFLINE_REDIRECT_REPLY,
    OFFLINE_STAGE_DONE_REPLY,
    proposeAndApprove,
    reachStage,
    registerNewUser,
    sendChatMessage,
    skipFlights,
    stageControls,
    stepper,
} from './helpers.ts'

// Every test starts with a new user (home: Philadelphia) and a new trip in the basics stage.
test.beforeEach(async ({ page }) => {
    await registerNewUser(page)
    await createTrip(page, 'Paris in spring')
})

const activityCards = (page: Page) =>
    page.getByRole('region', { name: 'Itinerary' }).locator('[data-item-type="activity"]')

test('walks the stages from basics to review', async ({ page }) => {
    const conversation = page.getByRole('log', { name: 'Conversation' })
    const itinerary = page.getByRole('region', { name: 'Itinerary' })
    const next = stageControls(page).getByRole('button', { name: 'Next' })
    const missing = stageControls(page).getByTestId('stage-missing')
    const noTransfer = itinerary.getByText('No transfer planned')

    // Basics: home is already the origin; dates, travellers and destination are missing.
    await expectStage(page, 'basics')
    await expect(next).toBeDisabled()
    await expect(missing).toContainText('trip dates')
    await expect(missing).not.toContainText('home')
    await proposeAndApprove(page, 'Set up the trip')
    await expect(conversation).toContainText(OFFLINE_STAGE_DONE_REPLY)
    await expect(next).toBeEnabled()
    await nextStage(page, 'flights')

    // Flights: driving instead, so the flights step reads "skipped".
    await expect(next).toBeDisabled()
    await skipFlights(page)
    await expect(stepper(page).getByRole('listitem').nth(1)).toContainText('Flights (skipped)')

    // Lodging: one hotel covering every night.
    await expect(missing).toContainText('a place to stay the night of')
    await proposeAndApprove(page, 'Add a hotel')
    await nextStage(page, 'activities')

    await addViaChat(page, 'a visit to the Louvre')
    await nextStage(page, 'ground')

    // Ground: the trip starts and ends at home, and every change of place lacks a transfer:
    // home → hotel, hotel ↔ Louvre, hotel → home.
    await expect(itinerary.getByTestId('home-chip')).toHaveCount(2)
    await expect(itinerary.getByTestId('home-chip').first()).toHaveText('Home · Philadelphia')
    await expect(noTransfer).toHaveCount(4)
    await expect(missing).toContainText('a transfer from Home (Philadelphia) to Hotel du Louvre')
    await expect(next).toBeDisabled()

    // One card proposes a taxi for every gap; approving it closes them all.
    await sendChatMessage(page, 'Add transfers')
    const proposal = latestProposal(page)
    await expect(proposal.getByText(/^Add transfer/)).toHaveCount(4)
    await approveProposal(page)
    await expect(noTransfer).toHaveCount(0)
    await expect(itinerary.locator('[data-item-type="transfer"]')).not.toHaveCount(0)
    await nextStage(page, 'review')

    await expect(stageControls(page).getByText('Trip complete')).toBeVisible()
    await expect(next).toHaveCount(0)

    // The stage is stored with the plan.
    await page.reload()
    await expectStage(page, 'review')
    await expect(page.getByRole('navigation', { name: 'Trips' })).toContainText('Review')
})

test("the assistant only uses the current stage's tools", async ({ page }) => {
    const conversation = page.getByRole('log', { name: 'Conversation' })

    // An activity belongs to the activities stage, so in basics the tool call is refused.
    await sendChatMessage(page, 'Add a visit to the Louvre')

    await expect(conversation).toContainText('not available in the basics stage')
    await expect(conversation.getByRole('region', { name: 'Proposed changes' })).toHaveCount(0)
    await expect(activityCards(page)).toHaveCount(0)
})

test('Back returns to an earlier stage', async ({ page }) => {
    await reachStage(page, 'lodging')

    await stageControls(page).getByRole('button', { name: 'Back', exact: true }).click()
    await expectStage(page, 'flights')
    await stepper(page)
        .getByRole('button', { name: /^Go back to Basics/ })
        .click()
    await expectStage(page, 'basics')
})

test('an off-topic message gets a gentle redirect and no itinerary change', async ({ page }) => {
    await sendChatMessage(page, "What's the capital of France?")

    const redirect = page
        .getByRole('log', { name: 'Conversation' })
        .getByRole('complementary', { name: 'Off-topic reply' })
    await expect(redirect).toContainText(OFFLINE_REDIRECT_REPLY)
    await expect(page.locator('[data-item-type]')).toHaveCount(0)
})

// Proposals of activities: the trip is set up, without flights, with a hotel.
test.describe('in the activities stage', () => {
    test.beforeEach(async ({ page }) => {
        await reachStage(page, 'activities')
    })

    test('a chat request proposes an item; approving puts it on the timeline', async ({ page }) => {
        const conversation = page.getByRole('log', { name: 'Conversation' })
        const itinerary = page.getByRole('region', { name: 'Itinerary' })
        const card = itinerary.locator('[data-item-type="activity"]', {
            hasText: 'Visit to the Louvre',
        })

        await sendChatMessage(page, 'Add a visit to the Louvre')

        // The change is only proposed: a neutral chip, a card to decide on, nothing on the timeline.
        // (`last()`: setting up the trip and the hotel left their own chips above.)
        await expect(
            conversation.getByRole('list', { name: 'Assistant actions' }).last(),
        ).toContainText('Proposed: Add activity “Visit to the Louvre”')
        await expect(conversation).toContainText(OFFLINE_PROPOSED_REPLY)
        const proposal = latestProposal(page)
        await expect(proposal).toContainText('Add activity “Visit to the Louvre”')
        await expect(card).toHaveCount(0)

        // Approving applies it: the plan_updated event puts the card on the timeline, no reload.
        await proposal.getByRole('button', { name: 'Approve' }).click()
        await expect(conversation).toContainText('Added activity “Visit to the Louvre”')
        await expect(conversation).toContainText(OFFLINE_ADDED_REPLY)
        await expect(proposal.getByText('Approved', { exact: true })).toBeVisible()
        await expect(card).toBeVisible()

        await card.click()
        const sheet = page.getByRole('dialog', { name: 'Visit to the Louvre' })
        await expect(sheet).toBeVisible()
        await expect(sheet).toContainText('Proposed')
        await page.keyboard.press('Escape')
        await expect(sheet).toBeHidden()

        // Everything is persisted: a reload shows the same conversation, the decided card and
        // the itinerary.
        await page.reload()
        await expect(conversation).toContainText('Add a visit to the Louvre')
        await expect(conversation).toContainText(OFFLINE_ADDED_REPLY)
        await expect(latestProposal(page).getByText('Approved', { exact: true })).toBeVisible()
        await expect(latestProposal(page).getByRole('button')).toHaveCount(0)
        await expect(card).toBeVisible()
    })

    test('denying a proposal changes nothing', async ({ page }) => {
        const conversation = page.getByRole('log', { name: 'Conversation' })

        await sendChatMessage(page, 'Add a visit to the Louvre')
        await latestProposal(page).getByRole('button', { name: 'Deny' }).click()

        await expect(conversation).toContainText(DENIED_REPLY)
        await expect(latestProposal(page).getByText('Denied', { exact: true })).toBeVisible()
        await expect(activityCards(page)).toHaveCount(0)
    })

    test('"Other…" replaces a proposal with a new one', async ({ page }) => {
        const conversation = page.getByRole('log', { name: 'Conversation' })
        const cards = conversation.getByRole('region', { name: 'Proposed changes' })
        // Cards from setting up the trip and the hotel are already in the log.
        const earlier = await cards.count()

        await sendChatMessage(page, 'Add a visit to the Louvre')
        await latestProposal(page).getByRole('button', { name: 'Other…' }).click()
        await page
            .getByRole('textbox', { name: 'What would you like instead?' })
            .fill('Add a visit to the Musée d’Orsay')
        await page.getByRole('button', { name: 'Send instead' }).click()

        // The old card is marked revised and a new, pending one proposes the replacement.
        await expect(cards).toHaveCount(earlier + 2)
        await expect(cards.nth(earlier).getByText('Revised', { exact: true })).toBeVisible()
        await expect(cards.last()).toContainText('Add activity “Visit to the Musée d’Orsay”')
        await expect(cards.last().getByRole('button', { name: 'Approve' })).toBeEnabled()
        await expect(activityCards(page)).toHaveCount(0)
    })

    test('"Fix it" on a warning asks the assistant without changing the plan', async ({ page }) => {
        const conversation = page.getByRole('log', { name: 'Conversation' })
        const itinerary = page.getByRole('region', { name: 'Itinerary' })

        const added = conversation.getByText(OFFLINE_ADDED_REPLY)
        // Earlier stages' approvals got the same reply.
        const earlier = await added.count()

        // The offline "add" command books both for 10:00-12:00 on day 2, so they overlap.
        await addViaChat(page, 'a visit to the Louvre')
        await expect(added).toHaveCount(earlier + 1)
        await addViaChat(page, 'a walk along the Seine')
        await expect(added).toHaveCount(earlier + 2)
        await expect(itinerary.locator('[data-item-type="activity"]')).toHaveCount(2)

        // The overlap, among the missing transfers between the places, in the stage bar's
        // warnings flyout.
        await page.getByRole('button', { name: /things to check$/ }).click()
        await page
            .getByRole('dialog', { name: 'Things to check' })
            .getByRole('button', {
                name: 'Fix it: Visit to the Louvre overlaps Walk along the Seine',
            })
            .click()

        await expect(conversation).toContainText(
            'Fix: Visit to the Louvre overlaps Walk along the Seine',
        )
        await expect(conversation).toContainText(OFFLINE_FIX_REPLY)
        await expect(itinerary.locator('[data-item-type="activity"]')).toHaveCount(2)
    })
})
