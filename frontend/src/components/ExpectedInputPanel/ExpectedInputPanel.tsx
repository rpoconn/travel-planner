import { ArrowRight, Minus, Plus, SkipForward } from 'lucide-react'
import { useState, type SyntheticEvent } from 'react'

import type { AskInput, FieldSpec } from '@/api/types.gen'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import {
    buildValue,
    canStep,
    earliestEnd,
    initialFormState,
    partKey,
    rangeSummary,
    setRangeStart,
    setText,
    stepCount,
    todayIso,
    togglePick,
    type FormState,
} from './ExpectedInputPanelService'

// The planner's pending question as a form, drawn from the slot's field specs: date ranges
// (from today on, with the trip's length shown), counts with -/+ steppers, money, text,
// places, and choice chips. Submitting sends the value straight to the
// planner (no model involved); an optional question can be skipped. Mount with
// `key={action.slot}` so the next question starts with a fresh form.

// ----- types -----

interface ExpectedInputPanelProps {
    action: AskInput
    /** Disables the form, e.g. while a turn streams. */
    disabled?: boolean
    onSubmit: (value: Record<string, unknown>) => void
    onSkip: () => void
}

export function ExpectedInputPanel({
    action,
    disabled = false,
    onSubmit,
    onSkip,
}: ExpectedInputPanelProps) {
    // ----- state -----
    const [form, setForm] = useState<FormState>(() =>
        initialFormState(action.fields, action.prefill),
    )
    const [error, setError] = useState<string | null>(null)
    // Read once: a trip can't start in the past (the server rejects that too).
    const [today] = useState(todayIso)

    // ----- event handlers -----
    function handleSubmit(event: SyntheticEvent<HTMLFormElement>) {
        event.preventDefault()
        const result = buildValue(action.fields, form)
        if (!result.ok) {
            setError(result.error)
            return
        }
        setError(null)
        onSubmit(result.value)
    }

    function updateText(key: string, value: string) {
        setForm((current) => setText(current, key, value))
    }

    function updateStart(name: string, value: string) {
        setForm((current) => setRangeStart(current, name, value))
    }

    function step(field: FieldSpec, delta: number) {
        setForm((current) => stepCount(current, field, delta))
    }

    function pick(field: FieldSpec, index: number) {
        setForm((current) => togglePick(current, field, index))
    }

    // ----- markup -----
    return (
        <form
            aria-label={action.prompt}
            onSubmit={handleSubmit}
            className="bg-card flex flex-col gap-3 rounded-xl border px-4 py-3 shadow-xs"
        >
            <p className="font-heading text-base">{action.prompt}</p>

            {action.fields.map((field) => (
                <fieldset key={field.name} disabled={disabled} className="flex flex-col gap-1.5">
                    <legend className="text-muted-foreground mb-1 text-xs font-medium">
                        {field.label}
                        {!field.required && ' (optional)'}
                    </legend>

                    {field.kind === 'date_range' && (
                        <div className="flex flex-wrap items-center gap-2">
                            <Input
                                type="date"
                                aria-label={`${field.label}: from`}
                                className="w-auto"
                                min={today}
                                value={form.text[partKey(field.name, 'start')] ?? ''}
                                onChange={(event) => {
                                    updateStart(field.name, event.target.value)
                                }}
                            />
                            <span className="text-muted-foreground text-xs">to</span>
                            <Input
                                type="date"
                                aria-label={`${field.label}: to`}
                                className="w-auto"
                                // The end can't come before the start.
                                min={earliestEnd(form, field.name) ?? today}
                                value={form.text[partKey(field.name, 'end')] ?? ''}
                                onChange={(event) => {
                                    updateText(partKey(field.name, 'end'), event.target.value)
                                }}
                            />
                            {rangeSummary(form, field.name) && (
                                <span aria-live="polite" className="text-muted-foreground text-xs">
                                    {rangeSummary(form, field.name)}
                                </span>
                            )}
                        </div>
                    )}

                    {field.kind === 'integer' && (
                        <div className="flex items-center gap-1">
                            <Button
                                type="button"
                                size="icon"
                                variant="outline"
                                aria-label={`Fewer ${field.label.toLowerCase()}`}
                                disabled={!canStep(form, field, -1)}
                                onClick={() => {
                                    step(field, -1)
                                }}
                            >
                                <Minus aria-hidden />
                            </Button>
                            <Input
                                type="number"
                                inputMode="numeric"
                                aria-label={field.label}
                                className="w-16 text-center"
                                min={field.min ?? undefined}
                                max={field.max ?? undefined}
                                value={form.text[field.name] ?? ''}
                                onChange={(event) => {
                                    updateText(field.name, event.target.value)
                                }}
                            />
                            <Button
                                type="button"
                                size="icon"
                                variant="outline"
                                aria-label={`More ${field.label.toLowerCase()}`}
                                disabled={!canStep(form, field, 1)}
                                onClick={() => {
                                    step(field, 1)
                                }}
                            >
                                <Plus aria-hidden />
                            </Button>
                        </div>
                    )}

                    {field.kind === 'money' && (
                        <div className="flex items-center gap-2">
                            <Input
                                type="number"
                                inputMode="decimal"
                                aria-label={field.label}
                                className="w-36"
                                min={0}
                                step="any"
                                value={form.text[partKey(field.name, 'amount')] ?? ''}
                                onChange={(event) => {
                                    updateText(partKey(field.name, 'amount'), event.target.value)
                                }}
                            />
                            <Input
                                aria-label={`${field.label}: currency`}
                                className="w-20 uppercase"
                                maxLength={3}
                                value={form.text[partKey(field.name, 'currency')] ?? ''}
                                onChange={(event) => {
                                    updateText(partKey(field.name, 'currency'), event.target.value)
                                }}
                            />
                        </div>
                    )}

                    {(field.kind === 'text' || field.kind === 'place') && (
                        <Input
                            aria-label={field.label}
                            placeholder={field.placeholder}
                            maxLength={200}
                            value={form.text[field.name] ?? ''}
                            onChange={(event) => {
                                updateText(field.name, event.target.value)
                            }}
                        />
                    )}

                    {(field.kind === 'choice' || field.kind === 'multi_choice') && (
                        <div
                            role={field.kind === 'choice' ? 'radiogroup' : 'group'}
                            aria-label={field.label}
                            className="flex flex-wrap gap-1.5"
                        >
                            {field.options.map((option, index) => {
                                const selected = (form.picks[field.name] ?? []).includes(index)
                                return (
                                    <Button
                                        key={String(option.value)}
                                        type="button"
                                        size="sm"
                                        variant={selected ? 'default' : 'outline'}
                                        // A choice is a radio group; a multi choice, toggles.
                                        role={field.kind === 'choice' ? 'radio' : undefined}
                                        aria-checked={
                                            field.kind === 'choice' ? selected : undefined
                                        }
                                        aria-pressed={
                                            field.kind === 'multi_choice' ? selected : undefined
                                        }
                                        onClick={() => {
                                            pick(field, index)
                                        }}
                                    >
                                        {option.label}
                                    </Button>
                                )
                            })}
                        </div>
                    )}
                </fieldset>
            ))}

            {error && (
                <p role="alert" className="text-destructive text-xs">
                    {error}
                </p>
            )}

            <div className="flex justify-end gap-1.5">
                {action.optional && (
                    <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={disabled}
                        onClick={onSkip}
                    >
                        <SkipForward aria-hidden />
                        Skip
                    </Button>
                )}
                <Button type="submit" size="sm" disabled={disabled}>
                    Continue
                    <ArrowRight aria-hidden />
                </Button>
            </div>
        </form>
    )
}
