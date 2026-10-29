import { expect, test } from '@playwright/test'

import {
    createTrip,
    OFFLINE_ADDED_REPLY,
    OFFLINE_REDIRECT_REPLY,
    registerNewUser,
    sendChatMessage,
} from './helpers.ts'

test.beforeEach(async ({ page }) => {
    await registerNewUser(page)
    await createTrip(page, 'Paris in spring')
})

test('a chat request adds an item that appears on the timeline', async ({ page }) => {
    const conversation = page.getByRole('log', { name: 'Conversation' })
    const itinerary = page.getByRole('region', { name: 'Itinerary' })

    await sendChatMessage(page, 'Add a visit to the Louvre')

    // The tool chip and the reply stream into the chat...
    await expect(conversation.getByRole('list', { name: 'Assistant actions' })).toContainText(
        'Added activity “Visit to the Louvre”',
    )
    await expect(conversation).toContainText(OFFLINE_ADDED_REPLY)

    // ...and the plan_updated event puts the card on the timeline without a reload.
    const card = itinerary.locator('[data-item-type="activity"]', {
        hasText: 'Visit to the Louvre',
    })
    await expect(card).toBeVisible()

    await card.click()
    const sheet = page.getByRole('dialog', { name: 'Visit to the Louvre' })
    await expect(sheet).toBeVisible()
    await expect(sheet).toContainText('Proposed')
    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden()

    // Everything is persisted: a reload shows the same conversation and itinerary.
    await page.reload()
    await expect(conversation).toContainText('Add a visit to the Louvre')
    await expect(conversation).toContainText(OFFLINE_ADDED_REPLY)
    await expect(card).toBeVisible()
})

test('an off-topic message gets a gentle redirect and no itinerary change', async ({ page }) => {
    await sendChatMessage(page, "What's the capital of France?")

    const redirect = page
        .getByRole('log', { name: 'Conversation' })
        .getByRole('complementary', { name: 'Off-topic reply' })
    await expect(redirect).toContainText(OFFLINE_REDIRECT_REPLY)
    await expect(page.locator('[data-item-type]')).toHaveCount(0)
})
