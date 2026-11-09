import {useEffect, useState} from 'react'
import {createPortal} from 'react-dom'
import {Plus, RefreshCw, Search} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Tabs, TabsList, TabsTrigger} from '../components/ui/tabs'
import {WorkItemsTable, type WorkItem} from '../journey/work-items-table'
import {workbenchItems} from './workbench-items'
import type {TaskSummary} from './task-summary'

export type WorkbenchProps = {
  tasks: TaskSummary[]; query: string; onQuery: (value: string) => void; onNew: () => void; onOpen: (href: string) => void
  loading: boolean; countsKnown: boolean; errors: string[]; busy: boolean; more: boolean; onMore: () => void; onRefresh: () => void
  legacyItems?: WorkItem[]; onResume?: () => void
  headerActions?: HTMLElement | null
}
const queues = [{id: 'all', label: '全部任务'}, {id: 'attention', label: '需要处理'}, {id: 'running', label: '进行中'}, {id: 'completed', label: '已完成'}] as const
const storedQueue = () => queues.find(row => row.id === history.state?.workbenchQueue)?.id || 'all'

/** Uses the original workbench table, file list, responsive cards and column controls. */
export function AuditWorkbench(props: WorkbenchProps) {
  const [queue, setQueue] = useState(storedQueue)
  useEffect(() => {
    const restoreQueue = () => setQueue(storedQueue())
    window.addEventListener('popstate', restoreQueue)
    return () => window.removeEventListener('popstate', restoreQueue)
  }, [])
  function chooseQueue(value: string) {
    const selected = queues.find(row => row.id === value)
    if (!selected) return
    setQueue(selected.id)
    history.replaceState({...history.state, workbenchQueue: selected.id}, '')
  }
  const items = [...workbenchItems(props.tasks), ...props.legacyItems || []]
  const shown = queue === 'all' ? items : items.filter(item => item.task === queue)
  const actions = <div className="flex flex-wrap gap-2">{props.onResume && <Button variant="outline" onClick={props.onResume}>继续当前审核</Button>}<Button onClick={props.onNew}><Plus/>新建审核</Button></div>
  return <section className="space-y-6" aria-label="工作台">
    {props.headerActions ? <><h1 className="sr-only">工作台</h1>{createPortal(actions, props.headerActions)}</> : <header className="flex flex-wrap items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">工作台</h1>{actions}</header>}
    <Tabs value={queue} onValueChange={chooseQueue}><TabsList>{queues.map(row => <TabsTrigger key={row.id} value={row.id}>{row.label}{/* A count shows only once every source is read; until then the tab says nothing rather than a placeholder. */}{props.countsKnown && !props.more && <span className="ml-1 text-xs tabular-nums text-muted-foreground">{row.id === 'all' ? items.length : items.filter(item => item.task === row.id).length}</span>}</TabsTrigger>)}</TabsList></Tabs>
    {props.errors.map(error => <p key={error} role="alert" className="text-sm text-destructive">{error}</p>)}
    {props.errors.length > 0 && items.length > 0 && <p className="text-sm text-muted-foreground">已读取的记录仍可打开；状态可能已变化。</p>}
    <WorkItemsTable items={shown} label={queues.find(row => row.id === queue)?.label || '全部'} onOpen={props.onOpen} loading={props.loading} readFailed={props.errors.length > 0} hasMore={props.more} busy={props.busy} onLoadMore={props.more ? props.onMore : undefined} toolbar={<><label className="flex min-w-0 flex-1 items-center gap-2 max-sm:basis-full sm:max-w-md"><Search className="size-4 shrink-0 text-muted-foreground"/><span className="sr-only">搜索审核任务</span><Input value={props.query} onChange={event => props.onQuery(event.target.value)} placeholder="搜索文件名或任务"/></label><Button variant="outline" size="icon" disabled={props.busy} aria-label="刷新任务列表" onClick={props.onRefresh}><RefreshCw className={props.busy ? 'animate-spin' : ''}/></Button></>}/>
  </section>
}
