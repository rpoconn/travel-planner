import type { ReactNode } from 'react'

interface EmptyPaneProps {
    icon: ReactNode
    title: string
    children: ReactNode
}

// Centered placeholder shown in a pane that has nothing to display yet.
export function EmptyPane({ icon, title, children }: EmptyPaneProps) {
    // ----- markup -----
    return (
        <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 p-6 text-center [&_svg]:size-8">
            {icon}
            <p className="text-foreground font-medium">{title}</p>
            <p className="max-w-xs text-sm">{children}</p>
        </div>
    )
}
