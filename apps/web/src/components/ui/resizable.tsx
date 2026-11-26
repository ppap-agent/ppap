import * as React from "react"
import { Group as PanelGroup, Panel, Separator as PanelResizeHandle } from "react-resizable-panels"
import { cn } from "@/lib/utils"
import { useNarrow } from "../narrow"

// Below 1280px side-by-side panes stack: each pane takes the whole width, in order, and nothing is dragged.
const Stacked = React.createContext(false)

function ResizablePanelGroup({ className, ...props }: React.ComponentProps<typeof PanelGroup>) {
  const stacked = useNarrow()
  if (stacked) return <Stacked.Provider value><div data-slot="resizable-panel-group" data-stacked="" aria-label={props["aria-label"]} className="flex w-full flex-col gap-4">{props.children}</div></Stacked.Provider>
  return <PanelGroup data-slot="resizable-panel-group" className={cn("flex size-full data-[orientation=vertical]:flex-col", className)} {...props} />
}

function ResizablePanel({ ...props }: React.ComponentProps<typeof Panel>) {
  const stacked = React.useContext(Stacked)
  if (stacked) return <div data-slot="resizable-panel" id={props.id} className="min-w-0">{props.children}</div>
  return <Panel data-slot="resizable-panel" {...props} />
}

function ResizableHandle({ className, withHandle, ...props }: React.ComponentProps<typeof PanelResizeHandle> & {withHandle?: boolean}) {
  const stacked = React.useContext(Stacked)
  if (stacked) return null
  return <PanelResizeHandle data-slot="resizable-handle" className={cn("relative flex w-px items-center justify-center bg-border outline-none after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:ring-3 focus-visible:ring-ring/50 data-[separator=active]:bg-primary", className)} {...props}>
    {withHandle && <div className="z-10 h-8 w-1 rounded-full bg-border" />}
  </PanelResizeHandle>
}

export {ResizablePanelGroup, ResizablePanel, ResizableHandle}
