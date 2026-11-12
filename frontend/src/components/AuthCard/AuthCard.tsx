import { Card, Text, Title } from '@mantine/core'
import { IconPlane } from '@tabler/icons-react'
import type { ReactNode } from 'react'

// Centered branded card shared by the login and register pages.

interface AuthCardProps {
    title: string
    description: string
    children: ReactNode
    footer: ReactNode
}

export function AuthCard({ title, description, children, footer }: AuthCardProps) {
    return (
        <main className="from-primary/10 via-background to-terracotta/15 flex min-h-screen flex-col items-center justify-center gap-6 bg-linear-to-br p-4">
            <div className="font-heading flex items-center gap-2 text-2xl">
                <span className="bg-primary text-primary-foreground flex size-9 items-center justify-center rounded-full">
                    <IconPlane className="size-4" aria-hidden />
                </span>
                Travel Planner
            </div>
            {/* Mantine's Card paints the page background and a grey border; the tokens make it
                read as a raised card like the app's others. */}
            <Card
                withBorder
                shadow="xs"
                radius="xl"
                padding="lg"
                className="bg-card border-border w-full max-w-sm"
            >
                <Title order={1} className="font-heading text-2xl">
                    {title}
                </Title>
                <Text size="sm" c="dimmed" className="mt-1">
                    {description}
                </Text>
                <div className="mt-6">{children}</div>
            </Card>
            <p className="text-muted-foreground text-sm">{footer}</p>
        </main>
    )
}
