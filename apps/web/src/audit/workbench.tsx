import {useState} from 'react'
import {Plus, RefreshCw, Search} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Tabs, TabsList, TabsTrigger} from '../components/ui/tabs'
import {WorkItemsTable, type WorkItem} from '../journey/work-items-table'
import {workbenchItems} from './workbench-items'
import type {TaskSummary} from './task-summary'

export type WorkbenchProps = {
  tasks: TaskSummary[]; query: string; onQuery: (value: string) => void; onNew: () => void; onOpen: (href: string) => void
  loading: boolean; errors: string[]; busy: boolean; more: boolean; onMore: () => void; onRefresh: () => void
  legacyItems?: WorkItem[]
}
const queues = [{id: 'all', label: '全部任务'}, {id: 'attention', label: '需要处理'}, {id: 'running', label: '进行中'}, {id: 'completed', label: '已完成'}] as const

/** Uses the original workbench table, file list, responsive cards and column controls. */
export function AuditWorkbench(props: WorkbenchProps) {
  const [queue, setQueue] = useState('all')
  const items = [...workbenchItems(props.tasks), ...props.legacyItems || []]
  const shown = queue === 'all' ? items : items.filter(item => item.task === queue)
  return <section className="mx-auto max-w-300 space-y-6" aria-label="工作台">
    <header className="flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">工作台</h1><Button onClick={props.onNew}><Plus/>新建审核</Button></header>
    <Tabs value={queue} onValueChange={setQueue}><TabsList>{queues.map(row => <TabsTrigger key={row.id} value={row.id}>{row.label}<span className="ml-1 text-xs tabular-nums text-muted-foreground">{row.id === 'all' ? items.length : items.filter(item => item.task === row.id).length}</span></TabsTrigger>)}</TabsList></Tabs>
    {props.errors.map(error => <p key={error} role="alert" className="text-sm text-destructive">{error}</p>)}
    <WorkItemsTable items={shown} label={queues.find(row => row.id === queue)?.label || '全部'} onOpen={props.onOpen} loading={props.loading} readFailed={props.errors.length > 0} hasMore={props.more} busy={props.busy} onLoadMore={props.more ? props.onMore : undefined} toolbar={<><label className="flex min-w-0 flex-1 items-center gap-2 sm:max-w-md"><Search className="size-4 shrink-0 text-muted-foreground"/><span className="sr-only">搜索审核任务</span><Input value={props.query} onChange={event => props.onQuery(event.target.value)} placeholder="搜索文件名或任务"/></label><Button variant="outline" size="icon" disabled={props.busy} aria-label="刷新任务列表" onClick={props.onRefresh}><RefreshCw className={props.busy ? 'animate-spin' : ''}/></Button></>}/>
    <p className="text-xs text-muted-foreground">人工复核可按需进行，不影响审核任务的完成状态。</p>
  </section>
}
