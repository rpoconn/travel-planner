import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

// Centers its chip on a short vertical line joining the cards above and below.

// ----- types -----

interface ConnectorRailProps {
    children: ReactNode
    /** Draw the line dashed (a missing transfer). */
    dashed?: boolean
}

export function ConnectorRail({ children, dashed = false }: ConnectorRailProps) {
    // ----- markup -----
    return (
        <div className="relative flex justify-center py-1">
            <span
                aria-hidden
                className={cn(
                    'border-border absolute inset-y-0 left-1/2 border-l-2',
                    dashed && 'border-dashed',
                )}
            />
            {/* Opaque backing so the line doesn't show through the translucent chip. */}
            <div className="bg-card relative max-w-full rounded-full">{children}</div>
        </div>
    )
}
