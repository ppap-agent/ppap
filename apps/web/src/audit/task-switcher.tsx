import {ChevronDown, Plus, Search} from 'lucide-react'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {FileName} from '../journey/file-name'
import {hasExecutionError, type TaskSummary} from './task-summary'

const stateTone = {matching: 'outline', pending_confirm: 'warning', reviewing: 'secondary', completed: 'outline'} as const

export function TaskSwitcher({open, onOpenChange, currentId, tasks, labels, query, onQuery, filter, onFilter, busy, error, hasMore, hasTasks, onMore, onOpen, onNew}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentId?: string
  tasks: TaskSummary[]
  labels: Record<TaskSummary['state'], string>
  query: string
  onQuery: (value: string) => void
  filter: string
  onFilter: (value: string) => void
  busy: boolean
  error: string
  hasMore: boolean
  hasTasks: boolean
  onMore: () => void
  onOpen: (id: string) => void
  onNew: () => void
}) {
  const current = tasks.find(row => row.id === currentId)
  // A task waiting on a person's confirmation stays stuck until they notice it, so it leads the list.
  const others = tasks.filter(row => row.id !== currentId)
  const recent = [...others.filter(row => row.state === 'pending_confirm'), ...others.filter(row => row.state !== 'pending_confirm')]
  const row = (task: TaskSummary) => <Button key={task.id} size="row" variant={task.id === currentId ? 'secondary' : 'ghost'} aria-current={task.id === currentId ? 'page' : undefined} className="min-w-0 items-start" onClick={() => onOpen(task.id)}>
    <span className="grid min-w-0 w-full gap-2">
      <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1"><span className="font-medium">{task.number}</span><span className="flex items-center gap-2 text-xs text-muted-foreground"><Badge shape="square" variant={hasExecutionError(task) ? 'destructive' : stateTone[task.state]}>{hasExecutionError(task) ? '审核异常' : labels[task.state]}</Badge>{task.files.filter(file => file.selection === 'selected').length} 份文件</span></span>
      <span className="grid gap-1.5 text-xs font-normal text-muted-foreground">{(task.id === currentId || query ? task.files : task.files.slice(0, 2)).map(file => <FileName truncate key={file.file.id} name={file.file.original_filename}/>)}{task.id !== currentId && !query && task.files.length > 2 && <span>另有 {task.files.length - 2} 份文件</span>}</span>
    </span>
  </Button>
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetTrigger asChild><Button variant="ghost" size="icon-sm" aria-label="打开审核任务"><ChevronDown aria-hidden="true"/></Button></SheetTrigger>
    <SheetContent side="left" className="w-full max-w-md sm:max-w-md">
      <div className="border-b pb-4"><SheetHeader><SheetTitle>审核任务</SheetTitle><SheetDescription className="sr-only">按任务编号或文件名查找并切换任务</SheetDescription></SheetHeader></div>
      <div className="grid shrink-0 gap-3 border-b pb-4">
        <Button variant="outline" onClick={onNew}><Plus aria-hidden="true"/>新建审核任务</Button>
        <label className="flex items-center gap-2"><span className="sr-only">搜索任务文件</span><Search aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/><Input placeholder="搜索任务编号或文件名" value={query} onChange={event => onQuery(event.target.value)}/></label>
        <Select value={filter} onValueChange={onFilter}><SelectTrigger className="w-full" aria-label="任务状态"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem>{Object.entries(labels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
      </div>
      <nav className="grid min-h-0 flex-1 content-start gap-5 overflow-y-auto" aria-label="任务列表">
        {current && <section className="grid gap-2"><h2 className="px-3 text-xs font-medium text-muted-foreground">当前任务</h2>{row(current)}</section>}
        {recent.length > 0 && <section className="grid gap-2"><h2 className="px-3 text-xs font-medium text-muted-foreground">最近任务</h2>{recent.map(row)}</section>}
        {busy && <ReadSkeleton label="正在读取任务…" kind={tasks.length ? 'inline' : 'list'}/>}
        {!tasks.length && !busy && <div className="grid justify-items-start gap-2"><p className="text-sm text-muted-foreground">{hasTasks ? '没有符合条件的任务。' : '开始识别后，任务显示在这里。'}</p>{(query || filter !== 'all') && <Button variant="outline" size="sm" onClick={() => {onQuery(''); onFilter('all')}}>清空条件</Button>}</div>}
      </nav>
      {(hasMore || error) && <div className="grid shrink-0 gap-3 border-t pt-4">{error && <p role="alert" className="text-xs">{error}</p>}<Button variant="outline" disabled={busy} onClick={onMore}>{busy ? '正在读取任务…' : error ? '重新读取任务列表' : '加载更多任务'}</Button></div>}
    </SheetContent>
  </Sheet>
}
import {ReadSkeleton} from '../components/read-skeleton'
