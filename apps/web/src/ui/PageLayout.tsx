import type {FormEventHandler, HTMLAttributes, ReactNode} from 'react'
import {cn} from '@/lib/utils'

export function PageLayout({children, className, ...props}: HTMLAttributes<HTMLElement>) {
  return <section className={cn('mx-auto flex w-full max-w-screen-2xl min-w-0 flex-col gap-6', className)} {...props}>{children}</section>
}

export function PageHeader({title, description, action, back, trailing}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  back?: ReactNode
  trailing?: ReactNode
}) {
  return <header className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div className="flex min-w-0 flex-col gap-1">
      {back && <div className="mb-1">{back}</div>}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{title}</h1>
        {trailing}
      </div>
      {description && <div className="text-sm text-muted-foreground">{description}</div>}
    </div>
    {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
  </header>
}

export function FilterBar({children, className, as = 'div', role, label, onSubmit}: {
  children: ReactNode
  className?: string
  as?: 'div' | 'form'
  role?: HTMLAttributes<HTMLDivElement>['role']
  label?: string
  onSubmit?: FormEventHandler<HTMLFormElement>
}) {
  const classes = cn('min-w-0', className)
  if (as === 'form') return <form role={role} aria-label={label} onSubmit={onSubmit} className={classes}>{children}</form>
  return <div role={role} aria-label={label} className={classes}>{children}</div>
}

export function DataSurface({children, className, ...props}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('w-full min-w-0 rounded-lg border bg-background', className)} {...props}>{children}</div>
}
