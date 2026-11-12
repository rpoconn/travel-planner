import {
    ActionIcon,
    Badge,
    Button,
    createTheme,
    type CSSVariablesResolver,
    Drawer,
    InputWrapper,
    localStorageColorSchemeManager,
    type MantineColorsTuple,
    Modal,
} from '@mantine/core'

// The Mantine theme, built from the same brand as the design tokens in index.css. Those tokens
// stay the source of truth for surfaces and text: the resolver at the bottom points Mantine's
// semantic colour variables at them, so Mantine components and Tailwind utilities agree in
// both colour schemes.

// ------------ Colours ------------

// Deep teal, generated from the oklch tokens in index.css. Mantine's colour helpers (darken,
// contrast) only parse hex/rgb/hsl, so the tuple is hex. Shade 6 matches light `--primary`,
// shade 3 matches dark `--primary`.
const brand: MantineColorsTuple = [
    '#e5f8f8',
    '#c7eded',
    '#a3dedd',
    '#5fbdb7',
    '#43a4a0',
    '#258484',
    '#006666',
    '#005454',
    '#004242',
    '#003131',
]

// ------------ Component defaults ------------

// Call sites pass bare icons, and these size any icon that doesn't set its own `size-*`
// class. Whole literals, not built from parts, so Tailwind's scanner finds them.
const ICON_3 = "[&_svg:not([class*='size-'])]:size-3"
const ICON_3_5 = "[&_svg:not([class*='size-'])]:size-3.5"
const ICON_4 = "[&_svg:not([class*='size-'])]:size-4"

/**
 * Text colour on a filled primary surface. Mantine's `autoContrast` only looks at the light
 * scheme's shade, so dark mode's light teal got white text; this token flips with the scheme.
 * Undefined leaves Mantine's own colour (outlines, other colours) in place.
 */
function onPrimaryFill(props: { variant?: string; color?: string }): string | undefined {
    const filled = (props.variant ?? 'filled') === 'filled'
    const primary = props.color === undefined || props.color === 'brand'
    return filled && primary ? 'var(--primary-foreground)' : undefined
}

const components = {
    // Most of the app's buttons sit in cards and toolbars, so the small size is the default;
    // full-size form buttons pass `size="sm"` (36px, the input height).
    Button: Button.extend({
        defaultProps: { size: 'xs' },
        vars: (_theme, props) => ({
            root: {
                // Mantine pairs its 30px button with xs text (11px); keep the sm text these had.
                '--button-fz': props.size === 'xs' ? 'var(--mantine-font-size-sm)' : undefined,
                '--button-color': onPrimaryFill(props),
            },
        }),
        classNames: (_theme, props) => ({
            section: props.size === 'compact-xs' ? ICON_3 : ICON_4,
        }),
    }),
    // 34px: header and toolbar icons. Steppers beside an input pass `size="input-sm"`.
    ActionIcon: ActionIcon.extend({
        defaultProps: { size: 'lg' },
        vars: (_theme, props) => ({ root: { '--ai-color': onPrimaryFill(props) } }),
        classNames: (_theme, props) => ({ root: props.size === 'sm' ? ICON_3_5 : ICON_4 }),
    }),
    Badge: Badge.extend({
        vars: (_theme, props) => ({ root: { '--badge-color': onPrimaryFill(props) } }),
        // Mantine badges are uppercase; the app's labels are sentence case.
        styles: { root: { textTransform: 'none' } },
        classNames: { section: ICON_3 },
    }),
    // TextInput and Textarea read these too.
    InputWrapper: InputWrapper.extend({
        defaultProps: {
            // Hints and errors sit under the field.
            inputWrapperOrder: ['label', 'input', 'description', 'error'],
            // Required fields aren't starred; optional ones say "(optional)" instead.
            withAsterisk: false,
        },
    }),
    // Overlays sit on the raised `--popover` surface, a step lighter than the page. The app's
    // drawers draw their own header (ItemDetailBody's is tinted per item type), so the close
    // button is pinned to the top-right corner rather than placed in Mantine's header row.
    Drawer: Drawer.extend({
        classNames: { content: 'relative bg-popover', close: 'absolute top-3 right-3' },
    }),
    DrawerCloseButton: Drawer.CloseButton.extend({
        defaultProps: { 'aria-label': 'Close' },
    }),
    // The header is sticky with its own background, so it needs the surface too.
    Modal: Modal.extend({
        classNames: { content: 'bg-popover', header: 'bg-popover' },
    }),
}

// ------------ Theme ------------

export const mantineTheme = createTheme({
    components,
    colors: { brand },
    primaryColor: 'brand',
    primaryShade: { light: 6, dark: 3 },
    // Light `--foreground`, used as the dark text on light fills.
    black: '#2c1e17',

    fontFamily: "'Inter Variable', ui-sans-serif, system-ui, sans-serif",
    headings: {
        fontFamily: "'Manrope Variable', ui-sans-serif, system-ui, sans-serif",
        fontWeight: '700',
    },
    // The same denser scale as the `--text-*` tokens in index.css.
    fontSizes: {
        xs: '0.6875rem',
        sm: '0.78125rem',
        md: '0.875rem',
        lg: '1rem',
        xl: '1.125rem',
    },
    // Unitless equivalents of the `--text-*--line-height` tokens.
    lineHeights: {
        xs: '1.45',
        sm: '1.44',
        md: '1.43',
        lg: '1.375',
        xl: '1.33',
    },

    // Multiples of `--radius` (0.875rem), matching the `--radius-*` tokens.
    radius: {
        xs: '0.35rem',
        sm: '0.525rem',
        md: '0.7rem',
        lg: '0.875rem',
        xl: '1.225rem',
    },
    defaultRadius: 'md',
    cursorType: 'pointer',
})

// ------------ CSS variables ------------

// Each value is a token from index.css, which already switches between light and dark, so the
// same map serves both schemes. It has to be set per scheme anyway: Mantine's defaults live
// in the scheme blocks and would override anything placed in the shared block.
const tokenVariables = {
    '--mantine-color-body': 'var(--background)',
    '--mantine-color-text': 'var(--foreground)',
    '--mantine-color-bright': 'var(--foreground)',
    '--mantine-color-dimmed': 'var(--muted-foreground)',
    '--mantine-color-placeholder': 'var(--muted-foreground)',
    '--mantine-color-anchor': 'var(--primary)',
    '--mantine-color-error': 'var(--destructive)',
    '--mantine-color-default': 'var(--card)',
    '--mantine-color-default-hover': 'var(--muted)',
    '--mantine-color-default-color': 'var(--foreground)',
    '--mantine-color-default-border': 'var(--input)',
    '--mantine-color-disabled': 'var(--muted)',
    '--mantine-color-disabled-color': 'var(--muted-foreground)',
    '--mantine-color-disabled-border': 'var(--border)',
} as const

export const cssVariablesResolver: CSSVariablesResolver = () => ({
    variables: {},
    light: tokenVariables,
    dark: tokenVariables,
})

// ------------ Colour scheme ------------

// Keeps the storage key the app used before Mantine (index.html's pre-paint script reads it
// too), so a saved light/dark choice carries over.
export const colorSchemeManager = localStorageColorSchemeManager({ key: 'travel-planner.theme' })
