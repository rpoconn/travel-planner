// Three bouncing dots shown while the assistant's reply hasn't produced any text yet.

// ----- constants -----

/** Stagger (ms) of each dot's bounce animation. */
const DOT_DELAYS = [0, 150, 300]

export function TypingDots() {
    // ----- markup -----
    return (
        <span role="status" aria-label="Assistant is typing" className="inline-flex gap-1 py-1">
            {DOT_DELAYS.map((delay) => (
                <span
                    key={delay}
                    className="bg-muted-foreground/60 size-1.5 animate-bounce rounded-full"
                    style={{ animationDelay: `${String(delay)}ms` }}
                />
            ))}
        </span>
    )
}
