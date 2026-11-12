import {ClipboardList, PanelLeftClose, Plus, Search} from 'lucide-react'
import {displayTime} from '../helpers'
import {ReadSkeleton} from '../components/read-skeleton'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {hasExecutionError, subjectLabel, type TaskSummary} from './task-summary'

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
  return <nav aria-label="审核任务列表" className="grid content-start gap-3 p-3">
    <div className="flex items-center justify-between gap-2 px-1"><h2 className="text-base font-semibold">审核任务</h2><div className="flex items-center gap-1">
      <Select value={filter} onValueChange={onFilter}><SelectTrigger size="sm" aria-label="任务状态"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部任务</SelectItem>{Object.entries(labels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
      <Button variant="quiet" size="xs" onClick={onCollapse}><PanelLeftClose aria-hidden="true"/>收起</Button>
    </div></div>
    <label className="flex items-center gap-2"><span className="sr-only">搜索审核任务</span><Search aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/><Input placeholder="搜索任务编号 / 车型 / 文件名" value={query} onChange={event => onQuery(event.target.value)}/></label>
    <Button variant="soft" size="lg" onClick={onNew}><Plus aria-hidden="true"/>新建审核任务</Button>
    <ul className="grid">{tasks.map(task => {
      const current = task.id === currentId, files = task.files.filter(file => file.selection === 'selected')
      return <li key={task.id}><button type="button" aria-current={current ? 'page' : undefined} onClick={() => onOpen(task.id)} className={`flex w-full items-start gap-2.5 rounded-md border px-2 py-3.5 text-left transition-colors ${current ? 'border-primary bg-primary-soft' : 'border-transparent border-b-border hover:bg-header'}`}>
        <ClipboardList aria-hidden="true" className="mt-0.5 size-4.5 shrink-0 text-primary"/><span className="grid min-w-0 flex-1 gap-1"><span className="flex items-center justify-between gap-2"><span className={`truncate text-sm ${current ? 'font-semibold text-primary' : 'font-medium'}`}>{task.number}</span><Badge variant={hasExecutionError(task) ? 'destructive' : task.state === 'completed' ? 'success' : task.state === 'pending_confirm' ? 'warning' : 'info'}>{hasExecutionError(task) ? '审核异常' : labels[task.state]}</Badge></span>
        {subjectLabel(task.subject) ? <span className="truncate text-xs text-foreground/80" title={subjectLabel(task.subject)}>{subjectLabel(task.subject)}</span> : files[0] && <span className="truncate text-xs text-muted-foreground">{files[0].file.original_filename}</span>}
        <span className="text-xs text-muted-foreground">共 {files.length} 份文件 · {displayTime(task.created_at)}</span></span>
      </button></li>
    })}</ul>
    {busy && <ReadSkeleton label="正在读取任务…" kind={tasks.length ? 'inline' : 'list'}/>}
    {!tasks.length && ready && !busy && !error && <div className="grid justify-items-start gap-2 px-1"><p className="text-sm text-muted-foreground">{hasTasks ? '没有符合条件的任务。' : '开始识别后，任务显示在这里。'}</p>{(query || filter !== 'all') && <Button variant="outline" size="sm" onClick={() => {onQuery(''); onFilter('all')}}>清空条件</Button>}</div>}
    {(hasMore || error) && <div className="grid gap-2 border-t pt-3">{error && <p role="alert" className="text-xs">{error}</p>}<Button variant="outline" size="sm" disabled={busy} onClick={onMore}>{busy ? '正在读取任务…' : error ? '重新读取任务列表' : '加载更多任务'}</Button></div>}
  </nav>
}
