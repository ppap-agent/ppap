// Reference: Stitch projects/13261964020862199279/screens/8996512ca72547bdb84f5399a3b95a22.
import {useMemo, useRef, type MouseEvent, type ReactNode} from 'react'
import {
  columnFilteringFeature, columnVisibilityFeature, createColumnHelper, createFilteredRowModel,
  createSortedRowModel, filterFn_equalsString, rowSortingFeature, sortFn_basic, tableFeatures, useTable,
} from '@tanstack/react-table'
import {ArrowDown, ArrowRight, ArrowUp, ArrowUpDown, Columns3} from 'lucide-react'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle} from '../components/ui/card'
import {DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuTrigger} from '../components/ui/dropdown-menu'
import {Progress} from '../components/ui/progress'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileCollection, type FileReference} from './file-collection'

export type WorkItem = {
  id: string; href: string; title: string; context: string; state: string; next: string; detail: string
  received: boolean; updatedAt: string
  task?: 'attention' | 'review' | 'prepare' | 'running' | 'completed'
  mode?: string
  counts?: Partial<Record<'NG' | 'UNABLE' | 'ERR' | 'PASS' | 'uncovered', number>>
  review?: {saved: number; total: number}
  files?: FileReference[]
}
export const workTask = (item: WorkItem) => item.task ?? (!item.received ? 'prepare' : 'review')
const resultLabels = {NG: '不满足', UNABLE: '无法判断', ERR: '执行失败', uncovered: '未覆盖', PASS: '满足'} as const
const columnLabels: Record<string, string> = {title: '审核资料', state: '检查结果', review: '人工复核', time: '时间', actions: '操作'}
const features = tableFeatures({
  columnFilteringFeature, columnVisibilityFeature, rowSortingFeature,
  filteredRowModel: createFilteredRowModel(), sortedRowModel: createSortedRowModel(),
  filterFns: {equalsString: filterFn_equalsString}, sortFns: {basic: sortFn_basic},
})
const helper = createColumnHelper<typeof features, WorkItem>()

const workTitle = (item: WorkItem) => item.files?.some(file => file.name === item.title) ? '' : item.title

function Results({item}: {item: WorkItem}) {
  const counts = Object.entries(resultLabels).flatMap(([key, label]) => {
    const count = item.counts?.[key as keyof typeof resultLabels]
    return count ? [<Badge key={key} variant={key === 'PASS' ? 'secondary' : 'outline'}>{label} {count}</Badge>] : []
  })
  const showState = !item.counts || ['attention', 'prepare', 'running'].includes(workTask(item))
  return <div className="space-y-1.5">
    <div className="flex flex-wrap gap-1.5">{showState && <Badge variant="outline">{item.state}</Badge>}{counts}{!showState && !counts.length && <span className="text-muted-foreground">无逐项结果</span>}</div>
    {item.detail && <p className="break-words text-xs leading-5 text-muted-foreground">{item.detail}</p>}
  </div>
}
function ReviewCount({item}: {item: WorkItem}) {
  return item.review && item.review.total > 0 ? <div className="w-20 space-y-2">
    <span className="tabular-nums"><span className="sr-only">人工复核已保存 </span>{item.review.saved} <span className="text-muted-foreground">/ {item.review.total}</span><span className="sr-only"> 项</span></span>
    <Progress aria-label="人工复核保存进度" value={Math.min(100, item.review.saved / item.review.total * 100)}/>
  </div> : <span className="text-muted-foreground">—<span className="sr-only">暂无人工复核计数</span></span>
}
function ItemTime({item}: {item: WorkItem}) {
  const date = new Date(item.updatedAt)
  return Number.isFinite(date.getTime()) ? <time dateTime={item.updatedAt} title={date.toLocaleString('zh-CN')} className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">{date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false})}</time> : <span className="text-muted-foreground">—</span>
}
function SortHeading({label, sorted, onSort}: {label: string; sorted: false | 'asc' | 'desc'; onSort?: (event: unknown) => void}) {
  const Icon = sorted === 'asc' ? ArrowUp : sorted === 'desc' ? ArrowDown : ArrowUpDown
  return <Button variant="ghost" size="sm" className="-ml-2" onClick={onSort}>{label}<Icon className="size-3.5 text-muted-foreground"/></Button>
}

export function WorkItemsTable({items, onOpen, label, loading, readFailed, hasMore, onLoadMore, busy, toolbar}: {
  items: WorkItem[]; onOpen: (href: string) => void; label: string
  loading?: boolean; readFailed?: boolean; hasMore?: boolean; onLoadMore?: () => void; busy?: boolean; toolbar: ReactNode
}) {
  const openItem = useRef(onOpen)
  openItem.current = onOpen
  function action(item: WorkItem) {
    const open = (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault(); openItem.current(item.href)
    }
    return <Button variant="ghost" size="sm" asChild className="-mr-2 shrink-0"><a href={item.href} onClick={open}>{item.next}<ArrowRight className="size-4"/></a></Button>
  }
  const columns = useMemo(() => helper.columns([
    helper.accessor('title', {
      enableHiding: false,
      sortFn: (a, b) => a.original.title.localeCompare(b.original.title, 'zh-CN', {numeric: true}),
      header: ({column}) => <SortHeading label="审核资料" sorted={column.getIsSorted()} onSort={column.getToggleSortingHandler()}/>,
      cell: ({row}) => <div className="min-w-0 space-y-1">{workTitle(row.original) && <p className="font-medium leading-5 [overflow-wrap:anywhere]">{workTitle(row.original)}</p>}{row.original.files?.length ? <FileCollection files={row.original.files} context={row.original.title}/> : null}{(row.original.context || row.original.mode) && <p className="break-words text-xs leading-5 text-muted-foreground">{[row.original.context, row.original.mode].filter(Boolean).join(' · ')}</p>}</div>,
    }),
    helper.accessor('state', {header: '检查结果', enableSorting: false, filterFn: 'equalsString', cell: ({row}) => <Results item={row.original}/>}),
    helper.accessor(row => row.review && row.review.total > 0 ? row.review.saved / row.review.total : -1, {
      id: 'review', sortFn: 'basic',
      header: ({column}) => <SortHeading label="人工复核" sorted={column.getIsSorted()} onSort={column.getToggleSortingHandler()}/>,
      cell: ({row}) => <ReviewCount item={row.original}/>,
    }),
    helper.accessor(row => Date.parse(row.updatedAt) || 0, {
      id: 'time', sortFn: 'basic',
      header: ({column}) => <SortHeading label="时间" sorted={column.getIsSorted()} onSort={column.getToggleSortingHandler()}/>,
      cell: ({row}) => <ItemTime item={row.original}/>,
    }),
    helper.display({id: 'actions', header: '操作', enableHiding: false, cell: ({row}) => action(row.original)}),
  ]), [])
  const table = useTable({features, data: items, columns, getRowId: item => item.id, initialState: {sorting: [{id: 'time', desc: true}]}, enableSortingRemoval: false, enableMultiSort: false})
  const rows = table.getRowModel().rows
  const states = useMemo(() => [...new Set(items.map(item => item.state))], [items])
  const stateFilter = String(table.getColumn('state')?.getFilterValue() || 'all')
  const order = table.state.sorting[0]
  const empty = loading ? '正在读取审核记录…' : readFailed ? '此列表尚未能完整读取，请重新读取。' : stateFilter !== 'all' ? '没有匹配此状态的已加载记录。' : hasMore ? '已加载记录中暂无此类审核。' : label === '全部' ? '暂无审核记录。' : `暂无${label}的记录。`
  return <div className="@container space-y-4">
    <div className="flex flex-wrap items-center gap-2">
      {toolbar}
      {(states.length > 1 || stateFilter !== 'all') && <Select value={stateFilter} onValueChange={value => table.getColumn('state')?.setFilterValue(value === 'all' ? undefined : value)}>
        <SelectTrigger aria-label="筛选审核状态"><SelectValue/></SelectTrigger>
        <SelectContent><SelectItem value="all">全部状态</SelectItem>{[...new Set([...states, ...(stateFilter !== 'all' ? [stateFilter] : [])])].map(state => <SelectItem key={state} value={state}>{state}</SelectItem>)}</SelectContent>
      </Select>}
      <div className="flex items-center gap-2 @min-[45rem]:ml-auto">
        <Select value={`${order?.id || 'time'}.${order?.desc ? 'desc' : 'asc'}`} onValueChange={value => {const [id, direction] = value.split('.'); table.setSorting([{id, desc: direction === 'desc'}])}}>
          <SelectTrigger aria-label="审核记录排序" className="@min-[45rem]:hidden"><SelectValue/></SelectTrigger>
          <SelectContent><SelectItem value="time.desc">时间从新到旧</SelectItem><SelectItem value="time.asc">时间从旧到新</SelectItem><SelectItem value="title.asc">资料名称升序</SelectItem><SelectItem value="title.desc">资料名称降序</SelectItem><SelectItem value="review.asc">复核进度升序</SelectItem><SelectItem value="review.desc">复核进度降序</SelectItem></SelectContent>
        </Select>
        <DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline" aria-label="选择显示列"><Columns3/>列显示</Button></DropdownMenuTrigger><DropdownMenuContent align="end">{table.getAllLeafColumns().filter(column => column.getCanHide()).map(column => <DropdownMenuCheckboxItem key={column.id} checked={column.getIsVisible()} onCheckedChange={value => column.toggleVisibility(Boolean(value))} onSelect={event => event.preventDefault()}>{columnLabels[column.id]}</DropdownMenuCheckboxItem>)}</DropdownMenuContent></DropdownMenu>
      </div>
    </div>
    <div className="hidden rounded-lg border @min-[45rem]:block">
      <Table className="table-fixed" aria-label={`${label}审核记录`}>
        <TableHeader>{table.getHeaderGroups().map(group => <TableRow key={group.id}>{group.headers.map(header => <TableHead key={header.id} scope="col" aria-sort={header.column.getIsSorted() === 'asc' ? 'ascending' : header.column.getIsSorted() === 'desc' ? 'descending' : undefined} className={header.column.id === 'title' ? 'w-auto' : header.column.id === 'actions' ? 'w-28 text-right' : header.column.id === 'state' ? 'w-40' : header.column.id === 'time' ? 'w-32' : 'w-28'}>{header.isPlaceholder ? null : <table.FlexRender header={header}/>}</TableHead>)}</TableRow>)}</TableHeader>
        <TableBody>{rows.length ? rows.map(row => <TableRow key={row.id}>{row.getVisibleCells().map(cell => <TableCell key={cell.id} className={`align-top whitespace-normal ${cell.column.id === 'actions' ? 'text-right' : ''}`}><table.FlexRender cell={cell}/></TableCell>)}</TableRow>) : <TableRow><TableCell colSpan={table.getVisibleLeafColumns().length} className="h-36 whitespace-normal text-center"><p className="text-muted-foreground">{empty}</p></TableCell></TableRow>}</TableBody>
      </Table>
    </div>
    <div className="space-y-3 @min-[45rem]:hidden" role={rows.length ? 'list' : undefined} aria-label={rows.length ? `${label}审核记录卡片` : undefined}>
      {rows.map(({original: item}) => <Card key={item.id} size="sm" role="listitem">
        <CardHeader>{workTitle(item) && <CardTitle className="min-w-0 [overflow-wrap:anywhere]">{workTitle(item)}</CardTitle>}{item.files?.length ? <FileCollection files={item.files} context={item.title}/> : null}{(item.context || item.mode) && <CardDescription className="break-words">{[item.context, item.mode].filter(Boolean).join(' · ')}</CardDescription>}</CardHeader>
        {(table.getColumn('state')?.getIsVisible() || table.getColumn('review')?.getIsVisible()) && <CardContent><div className="flex flex-wrap items-center justify-between gap-4">{table.getColumn('state')?.getIsVisible() && <Results item={item}/>} {table.getColumn('review')?.getIsVisible() && item.review && <div className="space-y-1.5"><p className="text-xs text-muted-foreground">人工复核</p><ReviewCount item={item}/></div>}</div></CardContent>}
        <CardFooter><div className="flex w-full flex-wrap items-center justify-between gap-2">{table.getColumn('time')?.getIsVisible() ? <ItemTime item={item}/> : <span/>}{action(item)}</div></CardFooter>
      </Card>)}
      {!rows.length && <p role="status" className="py-10 text-center text-sm text-muted-foreground">{empty}</p>}
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
      <p role="status">{loading ? '正在读取审核记录…' : `显示 ${rows.length} 项 · 已加载 ${items.length} 项`}{hasMore && <span className="ml-2">排序与状态筛选仅作用于已加载记录</span>}</p>
      {onLoadMore && <Button variant="outline" size="sm" disabled={busy} onClick={onLoadMore}>{busy ? '正在读取…' : '加载更多记录'}</Button>}
    </div>
  </div>
}
