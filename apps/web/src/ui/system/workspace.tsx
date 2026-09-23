import type {DragEvent, HTMLAttributes, ReactNode, Ref, UIEvent} from 'react'

type NoStyle<T> = Omit<T, 'className' | 'style' | 'color'>

export function Region({id, testId, hidden, children}: {id?: string; testId?: string; hidden?: boolean; children: ReactNode}) {
  return <section id={id} hidden={hidden} data-ui="region" data-testid={testId}>{children}</section>
}

export function Callout({tone = 'notice', children, id, testId}: {tone?: 'error' | 'notice' | 'loading'; children: ReactNode; id?: string; testId?: string}) {
  return <p id={id} data-ui="callout" data-tone={tone} role={tone === 'error' ? 'alert' : 'status'} data-testid={testId}>{children}</p>
}

export function Dropzone({
  active,
  children,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  active?: boolean
  children: ReactNode
  onDragOver: (event: DragEvent<HTMLDivElement>) => void
  onDragLeave: (event: DragEvent<HTMLDivElement>) => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
}) {
  return (
    <div data-ui="dropzone" data-active={active ? 'true' : undefined} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
      {children}
    </div>
  )
}

export function Material({children}: {children: ReactNode}) {
  return <article data-ui="material">{children}</article>
}

export function ActionBar({children}: {children: ReactNode}) {
  return <div data-ui="action-bar">{children}</div>
}

export function Workspace({
  preview,
  panel,
  resizing,
  rootRef,
  children,
}: {
  preview: number
  panel: string
  resizing: boolean
  rootRef: Ref<HTMLDivElement>
  children: ReactNode
}) {
  return (
    <div
      ref={rootRef}
      data-ui="workspace"
      data-panel={panel}
      data-resizing={resizing ? 'true' : undefined}
      style={{['--preview' as string]: `${preview}%`}}
    >
      {children}
    </div>
  )
}

export function Pane({kind, testId, children}: {kind: 'checks' | 'original'; testId?: string; children: ReactNode}) {
  return <section data-ui="pane" data-kind={kind} data-testid={testId}>{children}</section>
}

export function Resizer(props: NoStyle<HTMLAttributes<HTMLDivElement>>) {
  return <div data-ui="resizer" {...props} />
}

export function ViewTabs({children}: {children: ReactNode}) {
  return <nav data-ui="tabs" aria-label="审核视图">{children}</nav>
}

export function ViewTab({
  pressed,
  kind,
  children,
  onClick,
}: {
  pressed: boolean
  kind?: 'original'
  children: ReactNode
  onClick: () => void
}) {
  return <button data-ui="tab" data-kind={kind} type="button" aria-pressed={pressed} onClick={onClick}>{children}</button>
}

export function Toggle({
  pressed,
  children,
  testId,
  disabled,
  onClick,
}: {
  pressed: boolean
  children: ReactNode
  testId?: string
  disabled?: boolean
  onClick: () => void
}) {
  return <button data-ui="toggle" type="button" aria-pressed={pressed} data-testid={testId} disabled={disabled} onClick={onClick}>{children}</button>
}

export function ToggleRow({label, children}: {label: string; children: ReactNode}) {
  return <div data-ui="toggle-row" role="group" aria-label={label}>{children}</div>
}

export function Stat({value, label, tone, muted = false}: {value: number; label: string; tone?: 'success' | 'warning' | 'danger'; muted?: boolean}) {
  return (
    <span data-ui="stat" data-tone={muted ? undefined : tone} data-muted={muted ? 'true' : undefined}>
      <strong>{value}</strong>
      <span>{label}</span>
    </span>
  )
}

export function Meter({value, max, label}: {value: number; max: number; label: string}) {
  return (
    <div data-ui="meter">
      <span>{label}</span>
      <progress value={value} max={max} aria-label={label} />
    </div>
  )
}

export function StatRow({label, testId, children}: {label: string; testId?: string; children: ReactNode}) {
  return <div data-ui="stat-row" aria-label={label} data-testid={testId}>{children}</div>
}

export function TaskState({title, detail, badge}: {title: string; detail: string; badge?: ReactNode}) {
  return (
    <div data-ui="task" role="status">
      <span data-ui="task-indicator" aria-hidden="true"><span data-ui="task-spin" /></span>
      <span data-ui="task-copy"><strong>{title}</strong><span>{detail}</span></span>
      {badge}
    </div>
  )
}

export function FindingArticle({children}: {children: ReactNode}) {
  return <article data-ui="finding">{children}</article>
}

export function Evidence({children, testId}: {children: ReactNode; testId?: string}) {
  return <div data-ui="evidence" data-testid={testId}>{children}</div>
}

export function LockedFields({disabled, children}: {disabled?: boolean; children: ReactNode}) {
  return <fieldset data-ui="form" disabled={disabled}>{children}</fieldset>
}

export function ReviewForm({children, onSubmit}: {children: ReactNode; onSubmit: () => void}) {
  return <form data-ui="review" onSubmit={event => { event.preventDefault(); onSubmit() }}>{children}</form>
}

export function FindingContext({children}: {children: ReactNode}) {
  return <div data-ui="mobile-context-bar">{children}</div>
}

export function DraftLink({href, title, detail, action}: {href: string; title: string; detail: string; action: string}) {
  return (
    <a data-ui="draft-link" href={href}>
      <span><strong>{title}</strong><small>{detail}</small></span>
      <span>{action}</span>
    </a>
  )
}

export function ReportBlock({children, id}: {children: ReactNode; id?: string}) {
  return <article id={id} data-ui="report-block">{children}</article>
}

export function ReviewNote({judgment, rationale, nextStep}: {judgment: string; rationale?: string; nextStep?: string}) {
  return (
    <div data-ui="review-note">
      <strong>人工复核 · {judgment}</strong>
      {rationale ? <p>{rationale}</p> : null}
      {nextStep ? <p>下一步 · {nextStep}</p> : null}
    </div>
  )
}

export function ExcerptList({children, label, rootRef, onScroll}: {children: ReactNode; label: string; rootRef?: Ref<HTMLDivElement>; onScroll?: (event: UIEvent<HTMLDivElement>) => void}) {
  return <div ref={rootRef} data-ui="excerpt-list" aria-label={label} tabIndex={0} onScroll={onScroll}>{children}</div>
}

export function ExcerptRow({lookup, children}: {lookup?: boolean; children: ReactNode}) {
  return <article data-ui="excerpt-row" data-lookup={lookup ? 'true' : undefined} tabIndex={-1}>{children}</article>
}

export function ExcerptCells({children}: {children: ReactNode}) {
  return <dl data-ui="excerpt-cells">{children}</dl>
}

export function ExcerptCell({address, formula, value}: {address: string; formula?: boolean; value: string}) {
  return (
    <div data-ui="excerpt-cell" data-formula={formula ? 'true' : undefined}>
      <dt>{address}{formula ? <span>公式</span> : null}</dt>
      <dd>{formula ? <code>{value}</code> : value}</dd>
    </div>
  )
}
