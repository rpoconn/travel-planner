import { Sparkles } from 'lucide-react'

import type { TravelPlan } from '@/api/types.gen'
import { STAGE_META } from '@/lib/stages'

// What the chat shows before any message: a stage-specific prompt plus clickable suggestions.

// ----- types -----

interface EmptyChatProps {
    stage: TravelPlan['stage']
    suggestions: string[]
    onPick: (text: string) => void
}

// ----- helpers -----

/** Opening copy per stage; the first stage introduces how the assistant works. */
function emptyChatText(stage: TravelPlan['stage']): string {
    if (stage === 'basics') {
        return "Tell me where and when you'd like to go. I'll look things up and propose changes to the itinerary above; nothing is added until you approve it."
    }
    return `${STAGE_META[stage].description} Ask me and I'll propose options; nothing is added until you approve it.`
}

export function EmptyChat({ stage, suggestions, onPick }: EmptyChatProps) {
    // ----- markup -----
    return (
        <div className="m-auto flex max-w-sm flex-col items-center gap-4 text-center">
            <div className="bg-accent text-terracotta flex size-12 items-center justify-center rounded-full">
                <Sparkles className="size-6" aria-hidden />
            </div>
            <div>
                <p className="font-heading text-xl">{STAGE_META[stage].prompt}</p>
                <p className="text-muted-foreground mt-1 text-sm">{emptyChatText(stage)}</p>
            </div>
            <ul aria-label="Suggestions" className="flex w-full flex-col gap-2">
                {suggestions.map((suggestion) => (
                    <li key={suggestion}>
                        <button
                            type="button"
                            className="bg-card hover:border-primary/40 hover:bg-primary/5 w-full rounded-xl border px-4 py-2 text-left text-sm transition-colors"
                            onClick={() => {
                                onPick(suggestion)
                            }}
                        >
                            {suggestion}
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    )
}
