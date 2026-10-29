import { expect, test } from '@playwright/test'

import {
    addViaChat,
    createTrip,
    DENIED_REPLY,
    latestProposal,
    OFFLINE_ADDED_REPLY,
    OFFLINE_FIX_REPLY,
    OFFLINE_PROPOSED_REPLY,
    OFFLINE_REDIRECT_REPLY,
    registerNewUser,
    sendChatMessage,
} from './helpers.ts'

test.beforeEach(async ({ page }) => {
    await registerNewUser(page)
    await createTrip(page, 'Paris in spring')
})

test('a chat request proposes an item; approving puts it on the timeline', async ({ page }) => {
    const conversation = page.getByRole('log', { name: 'Conversation' })
    const itinerary = page.getByRole('region', { name: 'Itinerary' })
    const card = itinerary.locator('[data-item-type="activity"]', {
        hasText: 'Visit to the Louvre',
    })

    await sendChatMessage(page, 'Add a visit to the Louvre')

    // The change is only proposed: a neutral chip, a card to decide on, nothing on the timeline.
    await expect(conversation.getByRole('list', { name: 'Assistant actions' })).toContainText(
        'Proposed: Add activity “Visit to the Louvre”',
    )
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
    await expect(page.locator('[data-item-type]')).toHaveCount(0)
})

test('"Other…" replaces a proposal with a new one', async ({ page }) => {
    const conversation = page.getByRole('log', { name: 'Conversation' })
    const cards = conversation.getByRole('region', { name: 'Proposed changes' })

    await sendChatMessage(page, 'Add a visit to the Louvre')
    await latestProposal(page).getByRole('button', { name: 'Other…' }).click()
    await page
        .getByRole('textbox', { name: 'What would you like instead?' })
        .fill('Add a visit to the Musée d’Orsay')
    await page.getByRole('button', { name: 'Send instead' }).click()

    // The old card is marked revised and a new, pending one proposes the replacement.
    await expect(cards).toHaveCount(2)
    await expect(cards.first().getByText('Revised', { exact: true })).toBeVisible()
    await expect(cards.last()).toContainText('Add activity “Visit to the Musée d’Orsay”')
    await expect(cards.last().getByRole('button', { name: 'Approve' })).toBeEnabled()
    await expect(page.locator('[data-item-type]')).toHaveCount(0)
})

test('an off-topic message gets a gentle redirect and no itinerary change', async ({ page }) => {
    await sendChatMessage(page, "What's the capital of France?")

    const redirect = page
        .getByRole('log', { name: 'Conversation' })
        .getByRole('complementary', { name: 'Off-topic reply' })
    await expect(redirect).toContainText(OFFLINE_REDIRECT_REPLY)
    await expect(page.locator('[data-item-type]')).toHaveCount(0)
})

test('"Fix it" on a warning asks the assistant without changing the plan', async ({ page }) => {
    const conversation = page.getByRole('log', { name: 'Conversation' })
    const itinerary = page.getByRole('region', { name: 'Itinerary' })

    // The offline "add" command books both for 10:00-12:00 tomorrow, so they overlap.
    await addViaChat(page, 'a visit to the Louvre')
    await expect(conversation.getByText(OFFLINE_ADDED_REPLY)).toHaveCount(1)
    await addViaChat(page, 'a walk along the Seine')
    await expect(conversation.getByText(OFFLINE_ADDED_REPLY)).toHaveCount(2)
    await expect(itinerary.locator('[data-item-type="activity"]')).toHaveCount(2)

    // The overlap, plus a missing transfer between the two places.
    await itinerary.getByText('2 things to check').click()
    await itinerary
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
