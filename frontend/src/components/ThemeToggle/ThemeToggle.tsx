import { Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { toggleTheme, useTheme } from '@/lib/theme'

export function ThemeToggle() {
    // ----- state -----
    const dark = useTheme() === 'dark'

    // ----- markup -----
    return (
        <Button
            size="icon-sm"
            variant="ghost"
            aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
            onClick={toggleTheme}
        >
            {dark ? <Sun aria-hidden /> : <Moon aria-hidden />}
        </Button>
    )
}
