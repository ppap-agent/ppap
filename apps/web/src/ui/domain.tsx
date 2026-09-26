import {Children, useEffect, useState, type ReactNode, type Ref, type UIEvent} from 'react'
import {LoaderCircle} from 'lucide-react'
import {cn} from '@/lib/utils'
import {Badge} from '@/components/ui/badge'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog'
import {ScrollArea} from '@/components/ui/scroll-area'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle} from '@/components/ui/sheet'
import {Table, TableBody, TableCell, TableRow} from '@/components/ui/table'

/**
 * PPAP-only composition. These components own product behaviour (review panes,
 * evidence reading and the OnlyOffice host); interactive primitives remain the
 * generated shadcn/ui components imported by each page.
 */

export function TaskState({title, detail, badge}: {title: string; detail: string; badge?: ReactNode}) {
  return <div data-ui="task" role="status" className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-xl border bg-card p-3">
    <span className="grid size-8 place-items-center rounded-lg bg-primary/10" aria-hidden="true"><LoaderCircle className="size-4 animate-spin text-primary" /></span>
    <span className="grid gap-1"><strong>{title}</strong><span className="text-xs text-muted-foreground">{detail}</span></span>
    {badge}
  </div>
}

export function FindingArticle({findingKey, children}: {findingKey: string; children: ReactNode}) {
  return <article data-testid="finding-detail" data-finding-key={findingKey} className="flex flex-col gap-5">{children}</article>
}

export function ReviewColumns({detail, onDetailChange, count, children}: {detail: boolean; onDetailChange: (open: boolean) => void; count?: number; children: ReactNode}) {
  const parts = Children.toArray(children)
  const list = parts[0]
  const detailView = parts[1]
  const compactViewport = useCompactViewport()
  if (!compactViewport) return <div className="grid min-h-0 grid-cols-[minmax(13rem,18rem)_minmax(0,1fr)] gap-0">
    <aside className="min-w-0 border-r pr-4">
      <div className="flex h-[68dvh] min-h-96 flex-col">
        <div className="flex items-center justify-between border-b pb-3">
          <h2 className="text-sm font-semibold">检查项</h2>
          {count !== undefined && <Badge variant="secondary">{count}</Badge>}
        </div>
        <ScrollArea className="min-h-0 flex-1 mr-2"><div className="py-2">{list}</div></ScrollArea>
      </div>
    </aside>
    <section className="min-w-0 pl-6">{detailView}</section>
  </div>

  return <>
    <div>{list}</div>
    <Sheet open={detail} onOpenChange={onDetailChange}>
      <SheetContent side="bottom">
        <SheetHeader className="sr-only">
          <SheetTitle>检查项详情</SheetTitle>
          <SheetDescription>查看检查证据并填写人工复核。</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-hidden"><ScrollArea className="size-full">{detailView}</ScrollArea></div>
      </SheetContent>
    </Sheet>
  </>
}

export function ReviewList({children}: {children: ReactNode}) {
  return <nav aria-label="审核条款" className="flex min-w-0 flex-col">{children}</nav>
}

export function ReviewDetail({children}: {children: ReactNode}) {
  return <section aria-label="条款详情" className="min-w-0">{children}</section>
}

export function ReviewItem({selected, children, onClick}: {selected: boolean; children: ReactNode; onClick: () => void}) {
  return <button type="button" aria-current={selected ? 'true' : undefined} onClick={onClick} className={cn('flex min-h-11 w-full flex-col items-start gap-1 border-b px-2 py-2 text-left text-sm', selected ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-muted')}>{children}</button>
}

export function ReviewBack({children, onClick}: {children: ReactNode; onClick: () => void}) {
  return <button type="button" onClick={onClick} className="inline-flex min-h-11 items-center text-primary lg:hidden">{children}</button>
}

export function Evidence({children, testId}: {children: ReactNode; testId?: string}) {
  return <div data-testid={testId} className="flex min-w-0 flex-wrap items-start justify-between gap-3 border-y py-3 text-sm">{children}</div>
}

export function LockedFields({disabled, children}: {disabled?: boolean; children: ReactNode}) {
  return <fieldset disabled={disabled} className="min-w-0 border-0 p-0">{children}</fieldset>
}

export function ReviewForm({children, onSubmit}: {children: ReactNode; onSubmit: () => void}) {
  return <form onSubmit={event => {event.preventDefault(); onSubmit()}} className="flex flex-col gap-4 border-t pt-5">{children}</form>
}

export function FindingContext({children}: {children: ReactNode}) {
  return <div className="flex items-center justify-between gap-3 text-sm">{children}</div>
}

export function DraftLink({href, title, detail, action}: {href: string; title: string; detail: string; action: string}) {
  return <a href={href} className="flex items-center justify-between gap-4 border-b py-4 text-foreground no-underline hover:bg-muted/40"><span><strong className="block">{title}</strong><small className="block text-muted-foreground">{detail}</small></span><span className="text-primary">{action}</span></a>
}

export function ReportBlock({children, id}: {children: ReactNode; id?: string}) {
  return <article id={id} className="flex flex-col gap-1 border-t py-3">{children}</article>
}

export function ReviewNote({judgment, rationale, nextStep}: {judgment: string; rationale?: string; nextStep?: string}) {
  return <div className="border-l-2 border-primary/40 pl-3"><strong>人工复核 · {judgment}</strong>{rationale ? <p className="mt-1 whitespace-pre-wrap">{rationale}</p> : null}{nextStep ? <p className="mt-1">下一步 · {nextStep}</p> : null}</div>
}

export function ExcerptList({children, label, rootRef, onScroll}: {children: ReactNode; label: string; rootRef?: Ref<HTMLDivElement>; onScroll?: (event: UIEvent<HTMLDivElement>) => void}) {
  return <ScrollArea aria-label={label} tabIndex={0} viewportRef={rootRef} onViewportScroll={onScroll} variant="surface" className="h-[55dvh]">{children}</ScrollArea>
}

export function ExcerptRow({lookup, children}: {lookup?: boolean; children: ReactNode}) {
  return <article data-lookup={lookup ? 'true' : undefined} tabIndex={-1} className={cn('border-b px-3 py-2 text-xs last:border-b-0', lookup && 'bg-muted')}>{children}</article>
}

export function ExcerptCells({children}: {children: ReactNode}) { return <Table><TableBody>{children}</TableBody></Table> }

export function ExcerptCell({address, formula, value}: {address: string; formula?: boolean; value: string}) {
  return <TableRow><TableCell className="w-20 align-top"><span className="text-muted-foreground">{address}{formula ? <span className="ml-1">公式</span> : null}</span></TableCell><TableCell className="break-words">{formula ? <code>{value}</code> : value}</TableCell></TableRow>
}

export function PreviewFrame({title, src}: {title: string; src: string}) {
  return <iframe src={src} title={title} sandbox="allow-same-origin" referrerPolicy="no-referrer" className="h-[68dvh] min-h-96 w-full border-0" />
}

export function PlainText({children}: {children: ReactNode}) { return <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">{children}</pre> }
export function CodeText({children}: {children: ReactNode}) { return <code className="break-words font-mono text-xs">{children}</code> }

export function Pane({kind, testId, children}: {kind: 'checks' | 'original'; testId?: string; children: ReactNode}) {
  return <section data-testid={testId} className="min-w-0">{children}</section>
}

export function Workspace({panel, rootRef, children}: {panel: string; rootRef?: Ref<HTMLDivElement>; children: ReactNode}) {
  const parts = Children.toArray(children)
  const checks = parts[0]
  const original = parts[parts.length - 1]
  const hasOriginal = parts.length > 1

  const active = !hasOriginal || panel !== 'original' ? checks : original
  return <div ref={rootRef} data-panel={panel} className="min-w-0">
    {active}
  </div>
}

export function Modal({title, busy, onClose, children}: {title: string; busy?: boolean; onClose: () => void; children: ReactNode}) {
  return <Dialog open onOpenChange={open => {if (!open && !busy) onClose()}}><DialogContent onEscapeKeyDown={event => {if (busy) event.preventDefault()}}><DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription className="sr-only">对话框</DialogDescription></DialogHeader>{children}</DialogContent></Dialog>
}

export function OfficeFrame({children}: {children: ReactNode}) { return <div className="relative h-[min(72dvh,54rem)] min-h-[28rem] w-full overflow-hidden rounded-lg border bg-muted/20">{children}</div> }
export function OfficeHost({id}: {id: string}) { return <div id={id} className="size-full" /> }

export function isCompactViewport() { return typeof matchMedia === 'function' && matchMedia('(max-width: 1300px)').matches }

function useCompactViewport() {
  const [compact, setCompact] = useState(isCompactViewport)
  useEffect(() => {
    const query = matchMedia('(max-width: 1300px)')
    const update = () => setCompact(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return compact
}
