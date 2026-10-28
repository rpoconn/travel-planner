import { expect, test } from '@playwright/test'

test('app shell loads', async ({ page }) => {
    await page.goto('/')

    await expect(page.getByRole('heading', { name: 'Travel Planner' })).toBeVisible()
    await expect(page.getByTestId('api-status')).toBeVisible()
})
