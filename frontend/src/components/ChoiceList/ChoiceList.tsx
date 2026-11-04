import { ArrowRight } from 'lucide-react'
import { useState } from 'react'

import type { OfferChoice } from '@/api/types.gen'
import { CandidateCard } from '@/components/CandidateCard/CandidateCard'
import { Button } from '@/components/ui/button'

import {
    canSubmit,
    gapHint,
    isMulti,
    nightsHint,
    pickHint,
    submitLabel,
    toggleId,
} from './ChoiceListService'

// The planner's pending choices: the open offer's candidates, best first, under the
// question and what it's for (which nights, which gap). Picking is the
// approval, so a single pick is sent as soon as a card's Pick is pressed; a multi pick
// (activities) toggles cards and sends them with one button. The server checks the pick
// against the offer again. Mount with `key={action.offer_id}` so a new offer starts empty.

// ----- types -----

interface ChoiceListProps {
    action: OfferChoice
    /** Disables every pick, e.g. while a turn streams. */
    disabled?: boolean
    onPick: (candidateIds: string[]) => void
}

export function ChoiceList({ action, disabled = false, onPick }: ChoiceListProps) {
    // ----- state -----
    const [selected, setSelected] = useState<string[]>([])
    const multi = isMulti(action)
    const hint = pickHint(action)
    const nights = nightsHint(action)
    const route = gapHint(action)

    // ----- event handlers -----
    function pick(id: string) {
        if (multi) {
            setSelected((current) => toggleId(current, id, action.max_picks))
        } else {
            onPick([id])
        }
    }

    // ----- markup -----
    return (
        <section
            aria-label={action.prompt}
            className="bg-card flex flex-col gap-3 rounded-xl border px-4 py-3 shadow-xs"
        >
            <div>
                <p className="font-heading text-base">{action.prompt}</p>
                {nights && <p className="text-muted-foreground text-xs">{nights}</p>}
                {route && <p className="text-muted-foreground text-xs">{route}</p>}
                {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
            </div>

            <ul aria-label="Options" className="flex flex-col gap-2">
                {action.candidates.map((option) => (
                    <li key={option.id}>
                        <CandidateCard
                            candidate={option}
                            multi={multi}
                            selected={selected.includes(option.id)}
                            disabled={disabled}
                            onPick={() => {
                                pick(option.id)
                            }}
                        />
                    </li>
                ))}
            </ul>

            {multi && (
                <div className="flex justify-end">
                    <Button
                        type="button"
                        size="sm"
                        disabled={disabled || !canSubmit(selected, action)}
                        onClick={() => {
                            onPick(selected)
                        }}
                    >
                        {submitLabel(selected, action)}
                        <ArrowRight aria-hidden />
                    </Button>
                </div>
            )}
        </section>
    )
}
