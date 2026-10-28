import { describe, expect, it } from 'vitest'

import { errorMessage } from './apiError'

describe('errorMessage', () => {
    it('reads FastAPI HTTPException details', () => {
        expect(errorMessage({ detail: 'Incorrect email or password' })).toBe(
            'Incorrect email or password',
        )
    })

    it('reads the first validation error message', () => {
        const error = { detail: [{ loc: ['body', 'email'], msg: 'value is not a valid email' }] }

        expect(errorMessage(error)).toBe('value is not a valid email')
    })

    it('explains network failures', () => {
        expect(errorMessage(new TypeError('Failed to fetch'))).toMatch(/can't reach the server/i)
    })

    it('falls back for unknown shapes', () => {
        expect(errorMessage('boom', 'Fallback')).toBe('Fallback')
        expect(errorMessage({ detail: [] }, 'Fallback')).toBe('Fallback')
    })
})
