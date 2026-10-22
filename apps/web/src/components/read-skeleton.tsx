import {Skeleton} from './ui/skeleton'
import {cn} from '../lib/utils'

/** A read placeholder with one accessible status and no invented data. */
export function ReadSkeleton({label, kind = 'list', rows = 4, className}: {label: string; kind?: 'list' | 'table' | 'detail' | 'document' | 'inline'; rows?: number; className?: string}) {
  return <div role="status" aria-label={label} className={cn('min-w-0', className)}>
    <span className="sr-only">{label}</span>
    <div aria-hidden="true" className={kind === 'document' ? 'space-y-5 rounded-md border p-4 sm:p-6' : 'space-y-3'}>
      {kind === 'inline' ? <Skeleton className="h-4 w-36 max-w-full"/> : kind === 'document' ? <>
        <div className="flex gap-2 border-b pb-4"><Skeleton className="h-7 w-24"/><Skeleton className="h-7 w-32"/></div>
        <div className="mx-auto max-w-2xl space-y-6 py-6"><Skeleton className="h-8 w-2/3"/>{Array.from({length: rows}, (_, index) => <div key={index} className="space-y-3"><Skeleton className="h-4 w-1/3"/><Skeleton className="h-3 w-full"/><Skeleton className="h-3 w-5/6"/><Skeleton className="h-3 w-4/5"/></div>)}</div>
      </> : Array.from({length: rows}, (_, index) => <div key={index} className={cn('flex min-w-0 gap-4', kind === 'table' && 'items-center border-b py-3', kind === 'detail' && 'py-2')}>
        {kind === 'list' && <Skeleton className="size-9 shrink-0"/>}
        <div className="min-w-0 flex-1 space-y-2"><Skeleton className={cn('h-4 w-2/3', kind === 'detail' && 'h-3 w-20')}/><Skeleton className={cn('h-3 w-1/3', kind === 'detail' && 'h-4 w-3/4')}/></div>
        {kind === 'table' && <><Skeleton className="hidden h-4 w-1/4 sm:block"/><Skeleton className="h-4 w-16"/></>}
      </div>)}
    </div>
  </div>
}
