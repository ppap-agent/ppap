import {iconAnglesLeft, iconMagnifyingGlass, iconPlus} from '../components/icons'
import {Icon} from '../components/ui/icon'
import {displayTime} from '../helpers'
import {ReadSkeleton} from '../components/read-skeleton'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {useId} from 'react'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {hasExecutionError, subjectLabel, type TaskSummary} from './task-summary'

/** One color per task state, on the task list and the confirm-rules page alike. */
export const taskStateTone = (state: TaskSummary['state']) => state === 'completed' ? 'success' as const : state === 'pending_confirm' ? 'warning' as const : 'info' as const

/** The PPAP Audit task list: always beside the new-audit page and every task, so moving between tasks never leaves the page. */
export function TaskRail({currentId, tasks, labels, query, onQuery, filter, onFilter, busy, ready, error, hasMore, hasTasks, onMore, onOpen, onNew, onCollapse}: {
  currentId?: string
  tasks: TaskSummary[]
  labels: Record<TaskSummary['state'], string>
  query: string
  onQuery: (value: string) => void
  filter: string
  onFilter: (value: string) => void
  busy: boolean
  ready: boolean
  error: string
  hasMore: boolean
  hasTasks: boolean
  onMore: () => void
  onOpen: (id: string) => void
  onNew: () => void
  onCollapse: () => void
}) {
  return <nav aria-label="审核任务列表" className="flex min-h-0 flex-1 flex-col">
    <div className="flex items-center justify-between gap-2 border-b px-3.5 py-3"><h2 className="text-base font-semibold">审核任务</h2><div className="flex items-center gap-1">
      <Button variant="soft" size="xs" onClick={onNew}><Icon icon={iconPlus} aria-hidden="true"/>新建审核任务</Button>
      <Button variant="quiet" size="icon-xs" aria-label="收起审核任务" title="收起审核任务" onClick={onCollapse}><Icon icon={iconAnglesLeft} aria-hidden="true"/></Button>
    </div></div>
    <div className="flex items-center gap-2 border-b px-3 py-2">
      <div role="search" className="relative min-w-0 flex-1"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Icon icon={iconMagnifyingGlass} aria-hidden="true" className="size-3"/></span><Input type="search" iconStart aria-label="搜索审核任务" placeholder="搜索任务编号 / 车型 / 文件名" value={query} onChange={event => onQuery(event.target.value)}/></div>
      <Select value={filter} onValueChange={onFilter}><SelectTrigger size="sm" aria-label="任务状态"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部任务</SelectItem>{Object.entries(labels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      <ul className="grid gap-1.5">{tasks.map(task => <TaskRailItem key={task.id} task={task} current={task.id === currentId} labels={labels} onOpen={onOpen}/>)}</ul>
      {busy && <ReadSkeleton label="正在读取任务…" kind={tasks.length ? 'inline' : 'list'}/>}
      {!tasks.length && ready && !busy && !error && <div className="grid justify-items-start gap-2 p-1.5"><p className="text-xs text-muted-foreground">{hasTasks ? '没有符合条件的任务。' : '开始识别后，任务显示在这里。'}</p>{(query || filter !== 'all') && <Button variant="outline" size="sm" onClick={() => {onQuery(''); onFilter('all')}}>清除筛选</Button>}</div>}
      {(hasMore || error) && <div className="grid gap-2 border-t p-1.5 pt-3">{error && <p role="alert" className="text-xs text-destructive">{error}</p>}<Button variant="outline" size="sm" disabled={busy} onClick={onMore}>{busy ? '正在读取任务…' : error ? '重新读取任务列表' : '加载更多任务'}</Button></div>}
    </div>
  </nav>
}

/** One task: named by its number; state, subject, who started it (when not me) and file count describe it. */
function TaskRailItem({task, current, labels, onOpen}: {task: TaskSummary; current: boolean; labels: Record<TaskSummary['state'], string>; onOpen: (id: string) => void}) {
  const id = useId(), files = task.files.filter(file => file.selection === 'selected'), subject = subjectLabel(task.subject)
  // One line: what was audited (its subject, else its first file), who started it when not me, how many files, when.
  const detail = [subject || files[0]?.file.original_filename, task.creator.mine ? '' : `${task.creator.name || '同事'} 发起`, `共 ${files.length} 份文件`, displayTime(task.created_at)].filter(Boolean).join(' · ')
  return <li><button type="button" aria-current={current ? 'page' : undefined} aria-labelledby={`${id}-title`} aria-describedby={`${id}-state ${id}-detail`} onClick={() => onOpen(task.id)} className={`grid w-full gap-1.5 rounded-md border p-3 text-left transition-colors ${current ? 'border-primary-line bg-primary-soft' : 'border-transparent hover:border-border hover:bg-header'}`}>
    <span className="flex items-center justify-between gap-2"><span id={`${id}-title`} className={`truncate text-xs ${current ? 'font-semibold text-foreground' : 'font-medium'}`}>{task.number}</span><Badge id={`${id}-state`} variant={hasExecutionError(task) ? 'destructive' : taskStateTone(task.state)}>{hasExecutionError(task) ? '审核异常' : labels[task.state]}</Badge></span>
    <span id={`${id}-detail`} className="truncate text-xs text-muted-foreground" title={detail}>{detail}</span>
  </button></li>
}
