import { Check, TriangleAlert, X } from 'lucide-react'

import type { ChangesRecord } from '@/api/types.gen'

import { changesTitle } from './ChangesCardService'

// What a typed edit did ("remove the hotel"): one line per change written, with a check
// mark, one per ride the edit left missing, with a warning, and one per change the rules
// turned down, with the reason. There is nothing to
// approve here: the user's words were the approval, and the timeline already shows the
// result.

// ------------ Props ------------

interface ChangesCardProps {
    record: ChangesRecord
}

export function ChangesCard({ record }: ChangesCardProps) {
    // ------------ Initialization ------------
    const title = changesTitle(record)

    // ------------ Render ------------
    return (
        <section
            aria-label={title}
            data-role="assistant"
            className="bg-card flex w-fit max-w-[85%] flex-col gap-1 rounded-2xl rounded-bl-md border px-3 py-2 text-sm leading-snug shadow-xs"
        >
            <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                {title}
            </h3>
            <ul className="flex flex-col gap-1">
                {record.applied.map((change, index) => (
                    <li key={`applied-${String(index)}`} className="flex items-start gap-2">
                        <Check className="text-primary mt-0.5 size-4 shrink-0" aria-hidden />
                        <span>{change.summary}</span>
                    </li>
                ))}
                {record.left_open.map((note, index) => (
                    <li key={`left-open-${String(index)}`} className="flex items-start gap-2">
                        <TriangleAlert
                            className="text-warning mt-0.5 size-4 shrink-0"
                            aria-hidden
                        />
                        <span>{note}.</span>
                    </li>
                ))}
                {record.rejected.map((change, index) => (
                    <li key={`rejected-${String(index)}`} className="flex items-start gap-2">
                        <X className="text-destructive mt-0.5 size-4 shrink-0" aria-hidden />
                        <span className="flex flex-col">
                            <span>{change.summary}</span>
                            <span className="text-muted-foreground">{change.reason}</span>
                        </span>
                    </li>
                ))}
            </ul>
        </section>
    )
}
