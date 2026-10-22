import {LoaderCircle} from 'lucide-react'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Skeleton} from '../components/ui/skeleton'

export type TaskRecoveryState = {id: string; state: 'loading' | 'error' | 'unauthenticated' | 'forbidden' | 'not_found'; message: string; traceId?: string}

export function TaskRecovery({recovery, report, onRead, onSupport}: {recovery: TaskRecoveryState; report: boolean; onRead: () => void; onSupport: () => void}) {
  const loading = recovery.state === 'loading'
  const errors = {unauthenticated: '登录已失效', forbidden: '无法访问此任务', not_found: '未找到此任务', error: report ? '报告暂时无法打开' : '任务暂时无法打开'}
  const title = recovery.state === 'loading' ? report ? '审核报告' : '审核任务' : errors[recovery.state]
  return <section className="min-w-0 space-y-5" aria-label={report ? '报告读取状态' : '任务读取状态'}>
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none"/>{report ? '正在打开报告…' : '正在打开任务…'}</p>}
    </header>
    {loading ? <div aria-hidden="true" className="grid min-w-0 gap-6 min-[900px]:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="hidden space-y-5 border-r pr-5 min-[900px]:block">{[0, 1, 2].map(index => <div key={index} className="space-y-3 rounded-md border p-4"><Skeleton className="h-4 w-4/5"/><Skeleton className="h-4 w-3/5"/></div>)}</aside>
      <div className="min-w-0 space-y-4">
        <div className="flex items-center justify-between gap-6"><Skeleton className="h-6 w-2/5"/><Skeleton className="h-8 w-24"/></div>
        <div className="min-h-[55dvh] rounded-lg border bg-muted/30 p-5 sm:p-8"><div className="mx-auto max-w-xl space-y-6 rounded-md border bg-background p-5 sm:p-8"><Skeleton className="h-6 w-2/5"/><div className="space-y-3"><Skeleton className="h-3 w-full"/><Skeleton className="h-3 w-4/5"/></div>{[0, 1, 2].map(index => <Skeleton key={index} className="h-12 w-full"/>)}</div></div>
      </div>
    </div> : <Alert className="max-w-xl" variant={recovery.state === 'error' ? 'destructive' : 'default'}><AlertDescription><p>{recovery.message}</p><div className="mt-3 flex flex-wrap gap-2"><Button variant="outline" onClick={onRead}>{recovery.state === 'unauthenticated' ? '重新登录' : '重新读取'}</Button><Button variant="ghost" onClick={onSupport}>联系支持</Button></div></AlertDescription></Alert>}
  </section>
}
