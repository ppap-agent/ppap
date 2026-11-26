"use client"

import * as React from "react"
import { cn } from "@/lib/utils"
import { Tooltip as TooltipPrimitive } from "radix-ui"

function TooltipProvider({
  delayDuration = 0,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Provider>) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delayDuration={delayDuration}
      {...props}
    />
  )
}

// A touch screen has no hover: a tip whose trigger is not itself an action (a value with an explanation) opens and closes
// on tap; a tap elsewhere closes it. Buttons and links keep doing what they do (apps/web/README「窄屏不另起一套」).
const Toggle = React.createContext<(() => void) | null>(null)

function Tooltip({
  open,
  defaultOpen,
  onOpenChange,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  const [own, setOwn] = React.useState(defaultOpen ?? false)
  const shown = open ?? own
  const change = (next: boolean) => {if (open === undefined) setOwn(next); onOpenChange?.(next)}
  return <Toggle.Provider value={() => change(!shown)}><TooltipPrimitive.Root data-slot="tooltip" open={shown} onOpenChange={change} {...props} /></Toggle.Provider>
}

const isAction = (element: EventTarget) => element instanceof Element && element.matches("button, a, [role=button]")

function TooltipTrigger({
  onPointerDown,
  onClick,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Trigger>) {
  const toggle = React.useContext(Toggle), touch = React.useRef(false)
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props}
    onPointerDown={event => {onPointerDown?.(event); touch.current = event.pointerType === "touch"; if (touch.current && !isAction(event.currentTarget)) event.preventDefault()}}
    onClick={event => {onClick?.(event); if (touch.current && !isAction(event.currentTarget)) {event.preventDefault(); toggle?.()}}} />
}

function TooltipContent({
  className,
  sideOffset = 0,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        className={cn(
          "z-50 inline-flex w-fit max-w-xs origin-(--radix-tooltip-content-transform-origin) items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs text-background has-data-[slot=kbd]:pr-1.5 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 **:data-[slot=kbd]:relative **:data-[slot=kbd]:isolate **:data-[slot=kbd]:z-50 **:data-[slot=kbd]:rounded-sm data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-[state=delayed-open]:zoom-in-95 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
          className
        )}
        {...props}
      >
        {children}
        <TooltipPrimitive.Arrow className="z-50 size-2.5 translate-y-[calc(-50%_-_2px)] rotate-45 rounded-xs bg-foreground fill-foreground" />
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  )
}

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger }
