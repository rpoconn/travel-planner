import { ActionIcon, useComputedColorScheme, useMantineColorScheme } from '@mantine/core'
import { IconMoon, IconSun } from '@tabler/icons-react'

export function ThemeToggle() {
    // ----- setup -----
    const { setColorScheme } = useMantineColorScheme()
    // The scheme actually showing: "auto" resolved against the OS preference.
    const dark = useComputedColorScheme('light') === 'dark'

    // ----- handlers -----
    function toggle() {
        setColorScheme(dark ? 'light' : 'dark')
    }

    // ----- markup -----
    return (
        <ActionIcon
            variant="subtle"
            aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
            onClick={toggle}
        >
            {dark ? <IconSun aria-hidden /> : <IconMoon aria-hidden />}
        </ActionIcon>
    )
}
