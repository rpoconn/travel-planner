import type { Problem, TravelPlan } from '@/api/types.gen'

// Pure helpers for ProblemsPanel: the order the problems are listed in, and how the list is
// summed up.

// ------------ Listing ------------

/** What the trip is missing first (it keeps the trip from being complete), then warnings. */
export function sortProblems(problems: readonly Problem[]): Problem[] {
    const blocking = problems.filter((problem) => problem.severity === 'blocking')
    return [...blocking, ...problems.filter((problem) => problem.severity !== 'blocking')]
}

/** How many things the trip is still missing. */
export function leftCount(plan: TravelPlan): number {
    return plan.problems.filter((problem) => problem.severity === 'blocking').length
}

// ------------ Summary ------------

/** "1 thing left", "3 things left". */
export function summaryLabel(count: number): string {
    return `${String(count)} thing${count === 1 ? '' : 's'} left`
}
