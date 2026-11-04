import { CircleAlert, Compass, Flag } from 'lucide-react'

import type { ChatMessage } from '@/api/types.gen'
import { ToolChip } from '@/components/ToolChip/ToolChip'
import { TypingDots } from '@/components/TypingDots/TypingDots'
import type { ToolActivity } from '@/lib/chatTurn'

// One transcript entry. Renders stored messages and the live, streaming reply the same way:
// user bubbles on the right (including decisions on a proposal card and planning form
// answers), assistant replies on the left with tool chips above the text (a planning
// question, `expected_input`, reads as one too), off-topic redirects and planning nudges as
// a soft hint card, a planning stage move as a divider, and failures as an error notice.

// ----- types -----

interface MessageBubbleProps {
    role: ChatMessage['role']
    kind: ChatMessage['kind']
    content: string
    tools?: readonly ToolActivity[]
    /** The reply is still arriving: shows a typing indicator / caret. */
    streaming?: boolean
}

export function MessageBubble({
    role,
    kind,
    content,
    tools = [],
    streaming = false,
}: MessageBubbleProps) {
    // ----- markup -----
    if (role === 'user') {
        return (
            <div className="flex justify-end" data-role="user">
                <p className="bg-primary text-primary-foreground max-w-[85%] rounded-2xl rounded-br-md px-4 py-2 text-sm whitespace-pre-wrap">
                    {content}
                </p>
            </div>
        )
    }

    if (kind === 'stage') {
        // The planner moved on to another stage: a divider, not a message.
        return (
            <div
                role="separator"
                aria-label={content}
                data-role="system"
                className="text-muted-foreground flex items-center gap-3 py-1 text-xs"
            >
                <span className="bg-border h-px flex-1" aria-hidden />
                <span className="flex items-center gap-1.5">
                    <Flag className="size-3.5" aria-hidden />
                    {content}
                </span>
                <span className="bg-border h-px flex-1" aria-hidden />
            </div>
        )
    }

    if (role === 'system' || kind === 'error') {
        return (
            <div
                data-role="system"
                className="border-destructive/30 bg-destructive/5 text-destructive flex items-start gap-2 rounded-xl border px-3 py-2 text-sm"
            >
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
                <p className="whitespace-pre-wrap">{content}</p>
            </div>
        )
    }

    if (kind === 'redirect' || kind === 'nudge') {
        return (
            <aside
                // A nudge steers free text back to the planner's pending question.
                aria-label={kind === 'nudge' ? 'Back to the question' : 'Off-topic reply'}
                data-role="assistant"
                className="border-terracotta/40 bg-accent/60 text-accent-foreground flex max-w-[85%] items-start gap-3 rounded-2xl border border-dashed px-4 py-3 text-sm"
            >
                <Compass className="text-terracotta mt-0.5 size-4 shrink-0" aria-hidden />
                <p className="whitespace-pre-wrap">
                    {content || (streaming ? <TypingDots /> : null)}
                </p>
            </aside>
        )
    }

    const waiting = streaming && !content && tools.length === 0
    return (
        <div className="flex max-w-[85%] flex-col gap-2" data-role="assistant">
            {tools.length > 0 && (
                <ul aria-label="Assistant actions" className="flex flex-wrap gap-1.5">
                    {tools.map((tool) => (
                        // max-w-full: caps the item at the row's width so the chip's
                        // `truncate` kicks in, instead of a long summary overflowing the log.
                        <li key={tool.callId} className="max-w-full">
                            <ToolChip tool={tool} />
                        </li>
                    ))}
                </ul>
            )}
            {(content || waiting) && (
                <div className="bg-card rounded-2xl rounded-bl-md border px-4 py-2 text-sm shadow-xs">
                    {waiting ? (
                        <TypingDots />
                    ) : (
                        <p className="whitespace-pre-wrap">
                            {content}
                            {streaming && (
                                <span
                                    aria-hidden
                                    className="bg-foreground/60 ml-0.5 inline-block h-4 w-0.5 animate-pulse align-text-bottom"
                                />
                            )}
                        </p>
                    )}
                </div>
            )}
        </div>
    )
}
