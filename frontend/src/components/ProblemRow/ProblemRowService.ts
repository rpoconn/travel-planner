import type { Problem } from '@/api/types.gen'

// Pure helpers for ProblemRow: how a problem reads in the list, and what its button says.

// ------------ Text ------------

/**
 * The problem as a line of the list. A blocking problem's message names what's missing ("a
 * flight home"), so it is capitalised to stand alone; a warning's is a sentence already.
 */
export function problemText(problem: Problem): string {
    const { message } = problem
    if (problem.severity !== 'blocking' || message.length === 0) return message
    return `${message.charAt(0).toUpperCase()}${message.slice(1)}`
}

// ------------ Fixing ------------

/** Only what the trip is missing gets a button; a warning is the user's call. */
export function canFix(problem: Problem): boolean {
    return problem.severity === 'blocking'
}

/** The Fix this button's accessible name: "Fix this: a flight home". */
export function fixLabel(problem: Problem): string {
    return `Fix this: ${problem.message}`
}
