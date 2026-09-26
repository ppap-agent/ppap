import {dateLabel, type Round} from '../api'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Button} from '@/components/ui/button'
import {TaskState} from '../ui/domain'

export default function ExecutionState({
  execution,
  fileFindingCount,
  progress,
  failure,
  busy,
  emptyTitle,
  onShowAll,
  onShowOriginal,
  onPrepareAgain,
}: {
  execution: string
  fileFindingCount: number
  progress?: Round['progress']
  failure?: Round['failure']
  busy: boolean
  emptyTitle?: string
  onShowAll: () => void
  onShowOriginal: () => void
  onPrepareAgain: () => void
}) {
  if (execution === 'queued' || execution === 'running') {
    const stage = execution === 'queued' ? '等待执行' : progress ? ({reading: '读取资料', planning: '理解规则', checking: '执行检查', validating: '验证结果'} as const)[progress.phase] : '审核进行中'
    const count = progress?.completed_count !== undefined
      ? `已处理 ${progress.completed_count}${progress.total_count !== undefined ? ` / ${progress.total_count}` : ''} 条`
      : progress?.total_count !== undefined ? `共 ${progress.total_count} 条` : ''
    const heartbeat = progress?.updated_at ? `更新于 ${dateLabel(progress.updated_at)}` : ''
    return (
      <TaskState
        title={stage}
        detail={[count, heartbeat].filter(Boolean).join(' · ') || (execution === 'queued' ? '排队中' : '正在处理')}
      />
    )
  }

  if (execution === 'failed') {
    return (
      <Alert variant="destructive">
        <AlertTitle>本轮审核未完成</AlertTitle>
        <AlertDescription><div className="flex flex-wrap items-center gap-2">
          <span>{failure?.message || '执行失败，结果不完整。'}</span>
          <Button type="button" data-testid="prepare-again" disabled={busy} onClick={onPrepareAgain}>修改后重审</Button>
          <Button type="button" variant="ghost" onClick={onShowOriginal}>查看本轮资料</Button>
        </div></AlertDescription>
      </Alert>
    )
  }

  return (
    <Alert>
      <AlertTitle>{fileFindingCount ? '当前筛选下没有检查项' : emptyTitle || '本轮没有检查结果'}</AlertTitle>
      <AlertDescription>
        {fileFindingCount > 0 ? <Button type="button" size="sm" onClick={onShowAll}>查看全部检查项</Button> : <Button type="button" size="sm" onClick={onShowOriginal}>查看本轮资料</Button>}
      </AlertDescription>
    </Alert>
  )
}
