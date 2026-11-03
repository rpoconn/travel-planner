import * as React from 'react'
import { cn } from 'cn'
import { Popover as PopoverPrimitive } from 'radix-ui'

// shadcn-style popover over Radix: a floating panel anchored to its trigger, dismissed by
// Escape or a click outside.

function Popover({ ...props }: React.ComponentProps<typeof PopoverPrimitive.Root>) {
    return <PopoverPrimitive.Root data-slot="popover" {...props} />
}

function PopoverTrigger({ ...props }: React.ComponentProps<typeof PopoverPrimitive.Trigger>) {
    return <PopoverPrimitive.Trigger data-slot="popover-trigger" {...props} />
}

function PopoverContent({
    className,
    align = 'center',
    sideOffset = 4,
    ...props
}: React.ComponentProps<typeof PopoverPrimitive.Content>) {
    return (
        <PopoverPrimitive.Portal>
            <PopoverPrimitive.Content
                data-slot="popover-content"
                align={align}
                sideOffset={sideOffset}
                className={cn(
                    'bg-popover text-popover-foreground data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 z-50 w-72 origin-(--radix-popover-content-transform-origin) rounded-xl border p-4 text-sm shadow-md outline-hidden',
                    className,
                )}
                {...props}
            />
        </PopoverPrimitive.Portal>
    )
}

export { Popover, PopoverContent, PopoverTrigger }
