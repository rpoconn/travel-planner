import { Button, Group, Modal } from '@mantine/core'
import type { ReactNode } from 'react'

// "Are you sure?" before a destructive action: a title, what the action does, Cancel, and a
// red confirm button. Controlled: it closes only when the parent clears `opened`, so a parent
// whose action can fail keeps it open (with `loading`, then `error`) until the action succeeds.

// ----- props -----

interface ConfirmDialogProps {
    opened: boolean
    /** The question, e.g. Delete “Tokyo in May”? */
    title: ReactNode
    /** What confirming will do. */
    children: ReactNode
    /** The confirm button's label, e.g. Delete. */
    confirmLabel: string
    onConfirm: () => void
    /** Cancel, Escape, and a click outside all land here. */
    onCancel: () => void
    /** While the action runs: the confirm button spins and can't be pressed again. */
    loading?: boolean
    /** Why the action failed. */
    error?: string | undefined
}

export function ConfirmDialog({
    opened,
    title,
    children,
    confirmLabel,
    onConfirm,
    onCancel,
    loading = false,
    error,
}: ConfirmDialogProps) {
    // ----- render -----
    return (
        <Modal
            opened={opened}
            onClose={onCancel}
            title={title}
            centered
            withCloseButton={false}
            classNames={{ title: 'font-heading text-lg leading-snug' }}
        >
            <p className="text-muted-foreground text-sm">{children}</p>
            {error && (
                <p role="alert" className="text-destructive mt-3 text-sm">
                    {error}
                </p>
            )}
            <Group justify="flex-end" gap="xs" mt="lg">
                <Button variant="default" onClick={onCancel}>
                    Cancel
                </Button>
                {/* A fixed shade: plain "red" follows the theme's dark-scheme shade (3), a
                    pale pink too light for white text. red.9 matches light `--destructive`. */}
                <Button color="red.9" loading={loading} onClick={onConfirm}>
                    {confirmLabel}
                </Button>
            </Group>
        </Modal>
    )
}
