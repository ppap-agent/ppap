import type {ReactNode} from 'react'
import {ChevronRight} from 'lucide-react'
import {Button} from './ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from './ui/collapsible'

/** Secondary content behind one labelled button; the label says what opens, and its count when there is one. */
export function Disclosure({summary, children, className, defaultOpen, open, onOpenChange}: {summary: ReactNode; children: ReactNode; className?: string; defaultOpen?: boolean; open?: boolean; onOpenChange?: (open: boolean) => void}) {
  return <div className={className}>
    <Collapsible defaultOpen={defaultOpen} open={open} onOpenChange={onOpenChange}>
      <CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="group h-auto min-h-8 max-w-full justify-start whitespace-normal text-left"><ChevronRight aria-hidden="true" className="transition-transform group-data-[state=open]:rotate-90"/><span className="min-w-0">{summary}</span></Button></CollapsibleTrigger>
      {/* Like <details>, closed content stays in the page (hidden), so its state and text survive a collapse. */}
      <CollapsibleContent forceMount className="data-[state=closed]:hidden"><div className="space-y-3 pt-2">{children}</div></CollapsibleContent>
    </Collapsible>
  </div>
}
