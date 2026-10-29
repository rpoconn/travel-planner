import { expect, test } from '@playwright/test'

import { createTrip, PASSWORD, registerNewUser } from './helpers.ts'

test('a new account keeps its trips across sign-out and sign-in', async ({ page }) => {
    const email = await registerNewUser(page, 'Grace')
    await createTrip(page, 'Lisbon long weekend')

    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page).toHaveURL(/\/login$/)

    // Wrong password first: the error is shown and we stay on the login page.
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill('not my password')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert')).toContainText('Incorrect email or password')

    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: 'Sign in' }).click()

    await expect(page.getByTestId('current-user')).toHaveText('Grace')
    const trips = page.getByRole('navigation', { name: 'Trips' })
    await expect(trips.getByText('Lisbon long weekend')).toBeVisible()
})
