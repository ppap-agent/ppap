import type {ReactNode} from 'react'
import {cn} from '../lib/utils'

/** The one way rule pages count their clauses. */
export const clauseCount = (count: number) => `条款 ${count}`

/** Every rule page opens the same way: its name with the page's one action, then a line of facts. */
export function RuleDetailHeader({title, action, children}: {title: string; action?: ReactNode; children?: ReactNode}) {
  return <header className="space-y-3">
    <div className="flex flex-wrap items-start justify-between gap-4"><h1 className="min-w-0 break-words text-xl font-semibold">{title}</h1>{action}</div>
    {children && <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3">{children}</div>}
  </header>
}

export function RuleClauseList({className, children}: {className?: string; children: ReactNode}) {
  return <section aria-label="规则条款" className={cn('divide-y', className)}>{children}</section>
}

/**
 * One numbered clause. Its name shows only when it says something the text does not:
 * not when empty, not a bare number, and not when the text already starts with it.
 * `editor` replaces the name, text and source while the clause is edited; `details` and `aside` stay.
 */
export function RuleClause({number, title = '', text, source, editor, details, aside, issue = false}: {number: number; title?: string; text: string; source?: ReactNode; editor?: ReactNode; details?: ReactNode; aside?: ReactNode; issue?: boolean}) {
  const name = title.trim(), body = text.trim()
  const named = Boolean(name && body && !body.startsWith(name) && !new RegExp(`^(?:条款\\s*${number}|第\\s*${number}\\s*条|0*${number})$`).test(name))
  return <article aria-label={`第 ${number} 条`} data-issue={issue || undefined} className={cn('grid items-baseline gap-x-4 gap-y-3 py-5 data-issue:border-l-2 data-issue:border-l-destructive data-issue:pl-3', aside ? 'grid-cols-[1.5rem_minmax(0,1fr)_auto]' : 'grid-cols-[1.5rem_minmax(0,1fr)]')}>
    <span className="text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(number).padStart(2, '0')}</span>
    <div className="min-w-0 space-y-2">
      {editor ?? <>{named && <h2 className="break-words font-medium">{title}</h2>}<p className="whitespace-pre-wrap break-words leading-relaxed">{text || title || '还没有条款内容。'}</p>{source}</>}
      {details}
    </div>
    {aside && <div className="col-start-3 flex flex-col flex-wrap items-stretch gap-2">{aside}</div>}
  </article>
}
