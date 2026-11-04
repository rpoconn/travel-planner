import type { FieldSpec } from '@/api/types.gen'

// Pure form logic for ExpectedInputPanel: the form's state from a slot's fields and prefill,
// edits to it, and the value it submits (in the shape the slot's value model takes, see
// `planning.schemas.FieldKind` on the backend). The server checks the value again.

// ----- types -----

export interface FormState {
    /** Text inputs, keyed by field name, or `name.part` for a field with two inputs. */
    text: Record<string, string>
    /** Picked option indexes of choice / multi_choice fields, keyed by field name. */
    picks: Record<string, number[]>
}

export type FormResult = { ok: true; value: Record<string, unknown> } | { ok: false; error: string }

// ----- keys -----

/** Key of one input of a two-input field: a date range's start/end, money's amount/currency. */
export function partKey(name: string, part: 'start' | 'end' | 'amount' | 'currency'): string {
    return `${name}.${part}`
}

/** The first day a date range's end may be: its start, once one is picked. */
export function earliestEnd(state: FormState, name: string): string | undefined {
    // An empty input reads as no start yet (and an empty `min` would mean no limit anyway).
    const start = state.text[partKey(name, 'start')] ?? ''
    return start === '' ? undefined : start
}

// ----- dates -----

const DAY_MS = 24 * 60 * 60 * 1000
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** `now` as a local `YYYY-MM-DD`: the first day a trip may start (the server checks too). */
export function todayIso(now: Date = new Date()): string {
    const month = String(now.getMonth() + 1).padStart(2, '0')
    const day = String(now.getDate()).padStart(2, '0')
    return `${String(now.getFullYear())}-${month}-${day}`
}

/** An ISO date as a UTC timestamp, or null when it isn't one. UTC keeps day sums exact. */
function isoToUtc(iso: string): number | null {
    if (!ISO_DATE.test(iso)) return null
    const time = Date.parse(`${iso}T00:00:00Z`)
    return Number.isNaN(time) ? null : time
}

function utcToIso(time: number): string {
    return new Date(time).toISOString().slice(0, 10)
}

/** Nights from `start` to `end` (0 for a day trip), or null unless both are dates in order. */
export function nightsBetween(start: string, end: string): number | null {
    const from = isoToUtc(start)
    const to = isoToUtc(end)
    if (from === null || to === null || to < from) return null
    return Math.round((to - from) / DAY_MS)
}

/**
 * Set a date range's start. When both ends were already set, the end moves with it so the
 * trip keeps its length (as flight search sites do); otherwise an end before the new start
 * moves up to it.
 */
export function setRangeStart(state: FormState, name: string, start: string): FormState {
    const startKey = partKey(name, 'start')
    const endKey = partKey(name, 'end')
    const end = state.text[endKey] ?? ''
    const nights = nightsBetween(state.text[startKey] ?? '', end)
    const newStart = isoToUtc(start)
    let newEnd = end
    if (newStart !== null && nights !== null) {
        newEnd = utcToIso(newStart + nights * DAY_MS)
    } else if (newStart !== null && end !== '' && end < start) {
        newEnd = start
    }
    return { ...state, text: { ...state.text, [startKey]: start, [endKey]: newEnd } }
}

/** How long the picked range is ("7 nights", "Day trip"), or null until it is a range. */
export function rangeSummary(state: FormState, name: string): string | null {
    const nights = nightsBetween(
        state.text[partKey(name, 'start')] ?? '',
        state.text[partKey(name, 'end')] ?? '',
    )
    if (nights === null) return null
    if (nights === 0) return 'Day trip'
    return nights === 1 ? '1 night' : `${String(nights)} nights`
}

// ----- steppers -----

/** An integer field's count: empty or unreadable text counts as the field's minimum. */
function currentCount(state: FormState, field: FieldSpec): number {
    const raw = (state.text[field.name] ?? '').trim()
    const number = raw === '' ? NaN : Number(raw)
    return Number.isInteger(number) ? number : (field.min ?? 0)
}

/** Whether a stepper button can move the count by `delta` without leaving min..max. */
export function canStep(state: FormState, field: FieldSpec, delta: number): boolean {
    const next = currentCount(state, field) + delta
    if (field.min != null && next < field.min) return false
    if (field.max != null && next > field.max) return false
    return true
}

/** The count moved by `delta`, kept within the field's min..max. */
export function stepCount(state: FormState, field: FieldSpec, delta: number): FormState {
    let next = currentCount(state, field) + delta
    if (field.min != null) next = Math.max(field.min, next)
    if (field.max != null) next = Math.min(field.max, next)
    return setText(state, field.name, String(next))
}

// ----- reading prefill -----

function asRecord(value: unknown): Record<string, unknown> {
    return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}

/** A prefilled scalar as input text: numbers and strings as is, anything else empty. */
function asText(value: unknown): string {
    return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

// ----- initial state -----

/** The form as first shown: the plan's current values, or each field's natural default. */
export function initialFormState(
    fields: readonly FieldSpec[],
    prefill: Record<string, unknown>,
): FormState {
    const state: FormState = { text: {}, picks: {} }
    for (const field of fields) {
        const known = prefill[field.name]
        switch (field.kind) {
            case 'date_range': {
                const range = asRecord(known)
                state.text[partKey(field.name, 'start')] = asText(range.start)
                state.text[partKey(field.name, 'end')] = asText(range.end)
                break
            }
            case 'money': {
                const money = asRecord(known)
                state.text[partKey(field.name, 'amount')] = asText(money.amount)
                state.text[partKey(field.name, 'currency')] = asText(money.currency) || 'USD'
                break
            }
            case 'integer':
                // Counts start at their lowest allowed value (1 adult, 0 children).
                state.text[field.name] =
                    known === undefined ? asText(field.min ?? '') : asText(known)
                break
            case 'place':
                state.text[field.name] = asText(asRecord(known).name)
                break
            case 'text':
                state.text[field.name] = asText(known)
                break
            case 'choice': {
                const index = field.options.findIndex((option) => option.value === known)
                // Without a stored answer, a required choice starts on its first option (the
                // usual one: economy, any time), and an optional one on its "Any" (null).
                const fallback = field.required
                    ? 0
                    : field.options.findIndex((option) => option.value === null)
                const picked = field.name in prefill && index >= 0 ? index : fallback
                state.picks[field.name] = picked >= 0 ? [picked] : []
                break
            }
            case 'multi_choice': {
                const values = Array.isArray(known) ? (known as unknown[]) : []
                state.picks[field.name] = field.options.flatMap((option, index) =>
                    values.includes(option.value) ? [index] : [],
                )
                break
            }
        }
    }
    return state
}

// ----- edits -----

export function setText(state: FormState, key: string, value: string): FormState {
    return { ...state, text: { ...state.text, [key]: value } }
}

/** Pick an option: a choice replaces its pick; a multi choice toggles it, up to `max`. */
export function togglePick(state: FormState, field: FieldSpec, index: number): FormState {
    const current = state.picks[field.name] ?? []
    let next: number[]
    if (field.kind === 'choice') {
        next = [index]
    } else if (current.includes(index)) {
        next = current.filter((picked) => picked !== index)
    } else if (field.max != null && current.length >= field.max) {
        return state
    } else {
        next = [...current, index].sort((a, b) => a - b)
    }
    return { ...state, picks: { ...state.picks, [field.name]: next } }
}

// ----- submitting -----

const CURRENCY = /^[A-Z]{3}$/

function fail(error: string): FormResult {
    return { ok: false, error }
}

/**
 * The slot's value from the form, or the first problem with it. Empty optional fields are
 * left out, so the server's defaults apply.
 */
export function buildValue(fields: readonly FieldSpec[], state: FormState): FormResult {
    const value: Record<string, unknown> = {}
    for (const field of fields) {
        const label = field.label
        const text = (key: string) => (state.text[key] ?? '').trim()
        switch (field.kind) {
            case 'date_range': {
                const start = text(partKey(field.name, 'start'))
                const end = text(partKey(field.name, 'end'))
                if (!start || !end) {
                    if (field.required || start || end) return fail('Pick a start and an end date.')
                    break
                }
                // ISO dates compare correctly as strings.
                if (end < start) return fail('The trip has to end on or after the day it starts.')
                value[field.name] = { start, end }
                break
            }
            case 'integer': {
                const raw = text(field.name)
                if (!raw) {
                    if (field.required) return fail(`${label} is required.`)
                    break
                }
                const number = Number(raw)
                if (!Number.isInteger(number)) return fail(`${label} has to be a whole number.`)
                if (field.min != null && number < field.min)
                    return fail(`${label} has to be at least ${String(field.min)}.`)
                if (field.max != null && number > field.max)
                    return fail(`${label} can be at most ${String(field.max)}.`)
                value[field.name] = number
                break
            }
            case 'money': {
                const raw = text(partKey(field.name, 'amount'))
                if (!raw) {
                    if (field.required) return fail(`${label} is required.`)
                    break
                }
                const amount = Number(raw)
                if (!Number.isFinite(amount) || amount < 0)
                    return fail(`${label} has to be a positive number.`)
                const currency = text(partKey(field.name, 'currency')).toUpperCase() || 'USD'
                if (!CURRENCY.test(currency))
                    return fail('Use a three-letter currency code, like USD or EUR.')
                value[field.name] = { amount, currency }
                break
            }
            case 'text':
            case 'place': {
                const raw = text(field.name)
                if (!raw) {
                    if (field.required) return fail(`${label} is required.`)
                    break
                }
                value[field.name] = field.kind === 'place' ? { name: raw } : raw
                break
            }
            case 'choice': {
                const index = state.picks[field.name]?.[0]
                const option = index === undefined ? undefined : field.options[index]
                if (option === undefined) {
                    if (field.required) return fail(`Choose a ${label.toLowerCase()}.`)
                    break
                }
                value[field.name] = option.value
                break
            }
            case 'multi_choice': {
                const indexes = state.picks[field.name] ?? []
                const fewest = field.min ?? (field.required ? 1 : 0)
                if (indexes.length < fewest)
                    return fail(`Choose at least ${String(fewest)} ${label.toLowerCase()}.`)
                value[field.name] = indexes.flatMap((index) => {
                    const option = field.options[index]
                    return option ? [option.value] : []
                })
                break
            }
        }
    }
    return { ok: true, value }
}
