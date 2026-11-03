import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'

// Two panes stacked vertically with a draggable divider between them. The divider is a
// keyboard-accessible ARIA separator (arrow keys, Home/End). The split is remembered in
// localStorage so it survives reloads.

// ----- types -----

interface SplitPaneProps {
    top: ReactNode
    bottom: ReactNode
    /** Accessible name for the divider. */
    label?: string
    /** localStorage key for remembering the split. */
    storageKey?: string
    /** Initial share of the height given to the top pane (0–1). */
    defaultRatio?: number
}

/** Neither pane may shrink below this share of the height. */
const MIN_RATIO = 0.2
const MAX_RATIO = 0.8
/** Arrow-key step, as a share of the height. */
const KEY_STEP = 0.05

function clamp(ratio: number): number {
    return Math.min(MAX_RATIO, Math.max(MIN_RATIO, ratio))
}

function loadRatio(storageKey: string | undefined, fallback: number): number {
    if (storageKey === undefined) return fallback
    try {
        const stored = Number(window.localStorage.getItem(storageKey))
        return stored > 0 ? clamp(stored) : fallback
    } catch {
        return fallback // storage can be blocked (private mode)
    }
}

function saveRatio(storageKey: string | undefined, ratio: number) {
    if (storageKey === undefined) return
    try {
        window.localStorage.setItem(storageKey, String(ratio))
    } catch {
        // Not remembering the split is harmless.
    }
}

export function SplitPane({
    top,
    bottom,
    label = 'Resize panels',
    storageKey,
    defaultRatio = 0.4,
}: SplitPaneProps) {
    // ----- state -----
    const containerRef = useRef<HTMLDivElement>(null)
    const [ratio, setRatio] = useState(() => loadRatio(storageKey, clamp(defaultRatio)))
    const [dragging, setDragging] = useState(false)

    // ----- event handlers -----
    function update(next: number) {
        const clamped = clamp(next)
        setRatio(clamped)
        saveRatio(storageKey, clamped)
    }

    function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
        // Capture keeps move events coming even when the pointer leaves the thin handle.
        event.currentTarget.setPointerCapture(event.pointerId)
        setDragging(true)
    }

    function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
        if (!dragging || !containerRef.current) return
        const rect = containerRef.current.getBoundingClientRect()
        if (rect.height === 0) return
        update((event.clientY - rect.top) / rect.height)
    }

    function handlePointerUp(event: PointerEvent<HTMLDivElement>) {
        event.currentTarget.releasePointerCapture(event.pointerId)
        setDragging(false)
    }

    function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
        const next = {
            ArrowUp: ratio - KEY_STEP,
            ArrowDown: ratio + KEY_STEP,
            Home: MIN_RATIO,
            End: MAX_RATIO,
        }[event.key]
        if (next === undefined) return
        event.preventDefault()
        update(next)
    }

    // ----- markup -----
    return (
        <div ref={containerRef} className="flex h-full min-h-0 min-w-0 flex-col">
            {/* `basis-0` makes the flex-grow values the actual split, not content sizes. */}
            <div className="min-h-0 min-w-0 basis-0 overflow-hidden" style={{ flexGrow: ratio }}>
                {top}
            </div>

            <div
                role="separator"
                aria-orientation="horizontal"
                aria-label={label}
                aria-valuenow={Math.round(ratio * 100)}
                aria-valuemin={MIN_RATIO * 100}
                aria-valuemax={MAX_RATIO * 100}
                tabIndex={0}
                // `touch-none` stops the browser scrolling instead of dragging on touch screens.
                className={`focus-visible:bg-primary/40 hover:bg-primary/30 h-1.5 shrink-0 cursor-row-resize touch-none border-y transition-colors ${dragging ? 'bg-primary/30' : 'bg-border/50'}`}
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerUp}
                onKeyDown={handleKeyDown}
            />

            <div
                className="min-h-0 min-w-0 basis-0 overflow-hidden"
                style={{ flexGrow: 1 - ratio }}
            >
                {bottom}
            </div>
        </div>
    )
}
