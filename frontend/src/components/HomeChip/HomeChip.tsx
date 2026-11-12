import { IconHome } from '@tabler/icons-react'

import type { Place } from '@/api/types.gen'

// Where the trip leaves from and comes back to. Renders a list item, so it must sit directly
// inside the day's <ol>.

// ----- types -----

interface HomeChipProps {
    home: Place
}

export function HomeChip({ home }: HomeChipProps) {
    // ----- markup -----
    return (
        <li className="flex justify-center py-1">
            <span
                data-testid="home-chip"
                className="bg-secondary text-secondary-foreground inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                title={[home.address, home.city ?? home.name].filter(Boolean).join(', ')}
            >
                <IconHome className="size-3.5 shrink-0" aria-hidden />
                <span className="truncate">Home · {home.city ?? home.name}</span>
            </span>
        </li>
    )
}
