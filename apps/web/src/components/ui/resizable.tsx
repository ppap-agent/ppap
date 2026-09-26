import * as React from "react"
import { Group as PanelGroup, Panel, Separator as PanelResizeHandle } from "react-resizable-panels"
import { cn } from "@/lib/utils"

function ResizablePanelGroup({ className, ...props }: React.ComponentProps<typeof PanelGroup>) {
  return <PanelGroup data-slot="resizable-panel-group" className={cn("flex size-full data-[orientation=vertical]:flex-col", className)} {...props} />
}

function ResizablePanel({ ...props }: React.ComponentProps<typeof Panel>) {
  return <Panel data-slot="resizable-panel" {...props} />
}

function ResizableHandle({ className, withHandle, ...props }: React.ComponentProps<typeof PanelResizeHandle> & {withHandle?: boolean}) {
  return <PanelResizeHandle data-slot="resizable-handle" className={cn("relative flex w-px items-center justify-center bg-border outline-none after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:ring-3 focus-visible:ring-ring/50 data-[separator=active]:bg-primary", className)} {...props}>
    {withHandle && <div className="z-10 h-8 w-1 rounded-full bg-border" />}
  </PanelResizeHandle>
}

export {ResizablePanelGroup, ResizablePanel, ResizableHandle}
