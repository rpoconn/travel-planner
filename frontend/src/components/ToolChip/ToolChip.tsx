import {
    CircleCheck,
    ClipboardList,
    LoaderCircle,
    Search,
    TriangleAlert,
    type LucideIcon,
} from 'lucide-react'

import type { ToolActivity } from '@/lib/chatTurn'
import { cn } from '@/lib/utils'

// A pill summarising one assistant tool call (searching, adding a stay, ...) with its status.

// ----- types -----

interface ToolChipProps {
    tool: ToolActivity
}

// ----- constants -----

const STATUS_ICONS: Record<Exclude<ToolActivity['status'], 'ok'>, LucideIcon> = {
    running: LoaderCircle,
    failed: TriangleAlert,
    // Staged onto the proposal card, not applied: neutral rather than a check mark.
    proposed: ClipboardList,
}

export function ToolChip({ tool }: ToolChipProps) {
    // ----- state -----
    // Finished calls get a magnifier for searches and a check for everything else.
    const Icon =
        tool.status !== 'ok'
            ? STATUS_ICONS[tool.status]
            : tool.name === 'web_search'
              ? Search
              : CircleCheck

    // ----- markup -----
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
