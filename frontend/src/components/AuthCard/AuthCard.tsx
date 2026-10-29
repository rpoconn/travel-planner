import { Plane } from 'lucide-react'
import type { ReactNode } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

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
                    <Plane className="size-4" aria-hidden />
                </span>
                Travel Planner
            </div>
            <Card className="w-full max-w-sm">
                <CardHeader>
                    <CardTitle>
                        <h1 className="font-heading text-2xl font-normal">{title}</h1>
                    </CardTitle>
                    <CardDescription>{description}</CardDescription>
                </CardHeader>
                <CardContent>{children}</CardContent>
            </Card>
            <p className="text-muted-foreground text-sm">{footer}</p>
        </main>
    )
}
