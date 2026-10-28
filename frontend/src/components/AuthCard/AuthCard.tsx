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
        <main className="bg-muted/40 flex min-h-screen flex-col items-center justify-center gap-6 p-4">
            <div className="flex items-center gap-2 text-lg font-semibold">
                <Plane className="size-5" aria-hidden />
                Travel Planner
            </div>
            <Card className="w-full max-w-sm">
                <CardHeader>
                    <CardTitle>
                        <h1 className="text-xl">{title}</h1>
                    </CardTitle>
                    <CardDescription>{description}</CardDescription>
                </CardHeader>
                <CardContent>{children}</CardContent>
            </Card>
            <p className="text-muted-foreground text-sm">{footer}</p>
        </main>
    )
}
