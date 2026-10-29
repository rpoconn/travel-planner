import {
    CircleAlert,
    CircleCheck,
    ClipboardList,
    Compass,
    LoaderCircle,
    Search,
    TriangleAlert,
    type LucideIcon,
} from 'lucide-react'

import type { ChatMessage } from '@/api/types.gen'
import type { ToolActivity } from '@/lib/chatTurn'
import { cn } from '@/lib/utils'

// One transcript entry. Renders stored messages and the live, streaming reply the same way:
// user bubbles on the right (including decisions on a proposal card), assistant replies on
// the left with tool chips above the text, off-topic redirects as a soft hint card, and
// failures as an error notice.

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

    if (kind === 'redirect') {
        return (
            <aside
                aria-label="Off-topic reply"
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

// ----- subcomponents -----

const STATUS_ICONS: Record<Exclude<ToolActivity['status'], 'ok'>, LucideIcon> = {
    running: LoaderCircle,
    failed: TriangleAlert,
    // Staged onto the proposal card, not applied: neutral rather than a check mark.
    proposed: ClipboardList,
}

function ToolChip({ tool }: { tool: ToolActivity }) {
    const Icon =
        tool.status !== 'ok'
            ? STATUS_ICONS[tool.status]
            : tool.name === 'web_search'
              ? Search
              : CircleCheck

    return (
        <span
            data-status={tool.status}
            className={cn(
                'inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs',
                tool.status === 'running' && 'bg-muted text-muted-foreground',
                tool.status === 'ok' && 'border-primary/25 bg-primary/8 text-foreground',
                tool.status === 'proposed' && 'border-primary/25 bg-card text-muted-foreground',
                tool.status === 'failed' && 'border-warning/40 bg-warning/10 text-foreground',
            )}
        >
            <Icon
                aria-hidden
                className={cn(
                    'size-3.5 shrink-0',
                    tool.status === 'running' && 'animate-spin',
                    (tool.status === 'ok' || tool.status === 'proposed') && 'text-primary',
                    tool.status === 'failed' && 'text-warning',
                )}
            />
            <span className="truncate" title={tool.summary}>
                {tool.summary}
            </span>
        </span>
    )
}

function TypingDots() {
    return (
        <span role="status" aria-label="Assistant is typing" className="inline-flex gap-1 py-1">
            {[0, 150, 300].map((delay) => (
                <span
                    key={delay}
                    className="bg-muted-foreground/60 size-1.5 animate-bounce rounded-full"
                    style={{ animationDelay: `${String(delay)}ms` }}
                />
            ))}
        </span>
    )
}
