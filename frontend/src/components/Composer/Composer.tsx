import { ActionIcon, Textarea } from '@mantine/core'
import { IconSend2 } from '@tabler/icons-react'
import type { KeyboardEvent, SyntheticEvent } from 'react'

// Message box under the transcript. Controlled, so PlanningPanel can restore the text when
// sending fails. Enter sends; Shift+Enter adds a newline.

// ----- types -----

/** Mirrors the backend's `MAX_MESSAGE_CHARS`; longer messages are rejected with a 422. */
export const MAX_MESSAGE_CHARS = 4000
/** Show the character counter once the message gets close to the limit. */
const COUNTER_THRESHOLD = MAX_MESSAGE_CHARS - 500

interface ComposerProps {
    value: string
    onChange: (value: string) => void
    onSubmit: (value: string) => void
    /** True while a reply is streaming: typing is allowed, sending isn't. */
    busy?: boolean
    placeholder?: string
}

export function Composer({
    value,
    onChange,
    onSubmit,
    busy = false,
    placeholder = 'Ask about flights, stays, things to do…',
}: ComposerProps) {
    // ----- state -----
    const canSend = !busy && value.trim().length > 0

    // ----- event handlers -----
    function submit() {
        if (canSend) onSubmit(value)
    }

    function handleSubmit(event: SyntheticEvent<HTMLFormElement>) {
        event.preventDefault()
        submit()
    }

    function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        // isComposing: Enter that confirms an IME composition (e.g. Japanese input) must not send.
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            submit()
        }
    }

    // ----- markup -----
    return (
        <form onSubmit={handleSubmit} className="flex flex-col gap-1">
            <div className="bg-card focus-within:border-ring focus-within:ring-ring/50 flex items-end gap-2 rounded-2xl border p-2 shadow-xs transition-[box-shadow] focus-within:ring-3">
                {/* Unstyled: the surrounding box draws the border and focus ring. */}
                <Textarea
                    variant="unstyled"
                    autosize
                    minRows={1}
                    maxRows={6}
                    aria-label="Message"
                    maxLength={MAX_MESSAGE_CHARS}
                    value={value}
                    placeholder={placeholder}
                    className="min-w-0 flex-1"
                    classNames={{ input: 'px-2' }}
                    onChange={(event) => {
                        onChange(event.target.value)
                    }}
                    onKeyDown={handleKeyDown}
                />
                <ActionIcon
                    type="submit"
                    size="input-sm"
                    radius="xl"
                    className="shrink-0"
                    disabled={!canSend}
                    aria-label="Send message"
                >
                    <IconSend2 aria-hidden />
                </ActionIcon>
            </div>
            {value.length > COUNTER_THRESHOLD && (
                <p className="text-muted-foreground px-2 text-right text-xs">
                    {value.length} / {MAX_MESSAGE_CHARS}
                </p>
            )}
        </form>
    )
}
