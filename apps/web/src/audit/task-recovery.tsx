import {ReadSkeleton} from '../components/read-skeleton'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Button} from '../components/ui/button'

export type TaskRecoveryState = {id: string; state: 'loading' | 'error' | 'unauthenticated' | 'forbidden' | 'not_found'; message: string; traceId?: string}

export function TaskRecovery({recovery, report, onRead, onSupport}: {recovery: TaskRecoveryState; report: boolean; onRead: () => void; onSupport: () => void}) {
  const loading = recovery.state === 'loading'
  const errors = {unauthenticated: '登录已失效', forbidden: '无法访问此任务', not_found: '未找到此任务', error: report ? '报告暂时无法打开' : '任务暂时无法打开'}
  const title = recovery.state === 'loading' ? report ? '审核报告' : '审核任务' : errors[recovery.state]
  return <section className={loading ? 'min-w-0' : 'min-w-0 space-y-5'} aria-label={report ? '报告读取状态' : '任务读取状态'}>
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <h1 className={loading ? 'sr-only' : 'text-2xl font-semibold'}>{title}</h1>
    </header>
    {loading ? <ReadSkeleton label={report ? '正在打开报告…' : '正在打开任务…'} kind={report ? 'document' : 'detail'}/> : <Alert className="max-w-xl" variant={recovery.state === 'error' ? 'destructive' : 'default'}><AlertDescription><p>{recovery.message}</p><div className="mt-3 flex flex-wrap gap-2"><Button variant="outline" onClick={onRead}>{recovery.state === 'unauthenticated' ? '重新登录' : '重新读取'}</Button><Button variant="ghost" onClick={onSupport}>联系支持</Button></div></AlertDescription></Alert>}
  </section>
}
