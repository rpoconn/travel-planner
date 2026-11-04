import { Check, ExternalLink, Plus, TriangleAlert } from 'lucide-react'

import type { Candidate } from '@/api/types.gen'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { pickLabel, priceLabel } from './CandidateCardService'

// One option in the planner's choices: a flight, a stay, an activity or a way to get
// somewhere. It shows what the search found (title, times, facts, price), how it misses
// what the user asked for, and the button that picks it. Picking is the approval.

// ----- types -----

interface CandidateCardProps {
    candidate: Candidate
    /** Several can be picked: the button toggles instead of picking at once. */
    multi?: boolean
    selected?: boolean
    disabled?: boolean
    onPick: () => void
}

export function CandidateCard({
    candidate,
    multi = false,
    selected = false,
    disabled = false,
    onPick,
}: CandidateCardProps) {
    // ----- state -----
    const price = priceLabel(candidate)
    const label = pickLabel(multi, selected)

    // ----- markup -----
    return (
        <article
            aria-label={candidate.title}
            className={cn(
                'bg-card flex flex-col gap-2 rounded-lg border px-3 py-2.5 sm:flex-row sm:items-start',
                selected && 'border-primary ring-primary/30 ring-2',
            )}
        >
            <div className="flex min-w-0 flex-1 flex-col gap-1">
                <p className="text-sm font-medium">{candidate.title}</p>
                {candidate.subtitle && (
                    <p className="text-muted-foreground text-xs">{candidate.subtitle}</p>
                )}
                {candidate.details.length > 0 && (
                    <ul className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                        {candidate.details.map((detail) => (
                            <li key={detail}>{detail}</li>
                        ))}
                    </ul>
                )}
                {candidate.warnings.length > 0 && (
                    <ul aria-label="Not what you asked for" className="flex flex-col gap-0.5">
                        {candidate.warnings.map((warning) => (
                            <li
                                key={warning}
                                className="text-terracotta flex items-center gap-1 text-xs"
                            >
                                <TriangleAlert className="size-3 shrink-0" aria-hidden />
                                {warning}
                            </li>
                        ))}
                    </ul>
                )}
                {candidate.source_url && (
                    <a
                        href={candidate.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary inline-flex w-fit items-center gap-1 text-xs underline-offset-2 hover:underline"
                    >
                        Details
                        <ExternalLink className="size-3" aria-hidden />
                    </a>
                )}
            </div>

            <div className="flex shrink-0 items-center gap-3 sm:flex-col sm:items-end">
                {price && <p className="text-sm font-semibold tabular-nums">{price}</p>}
                <Button
                    type="button"
                    size="sm"
                    variant={multi && !selected ? 'outline' : 'default'}
                    disabled={disabled}
                    aria-pressed={multi ? selected : undefined}
                    // "Pick" alone is ambiguous in a list of several: name the option.
                    aria-label={`${label} ${candidate.title}`}
                    onClick={onPick}
                >
                    {multi && (selected ? <Check aria-hidden /> : <Plus aria-hidden />)}
                    {label}
                </Button>
            </div>
        </article>
    )
}
