import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Reference: Stitch projects/13261964020862199279/screens/8996512ca72547bdb84f5399a3b95a22.
import {useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode} from 'react'
import {ArrowRight, BookOpen, ClipboardCheck, LayoutDashboard, RefreshCw, Search, UploadCloud} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {Input} from '../components/ui/input'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {AccountMenu, isPreview} from './access'
import {fileAccept, useLocalUploads, type LocalUpload} from './local-files'
import {WorkItemsTable, workTask, type WorkItem} from './work-items-table'

const routeView = () => {const value = new URLSearchParams(location.search).get('view'); return value === 'usage' || value === 'requirements' ? value : 'workbench'}
const routeQueue = (): 'attention' | 'review' | 'all' | null => {
  const params = new URLSearchParams(location.search), value = params.get('queue')
  return params.get('view') === 'cases' ? 'all' : value === 'attention' || value === 'review' || value === 'all' ? value : null
}
export function TaskWorkbench({items, onOpen, onFiles, onDemo, requirements, usage, search, onSearch, loading = false, onLoadMore, loadingMore = false, refreshing = false, errors = [], onRefresh}: {
  items: WorkItem[]
  onOpen: (href: string) => void
  onFiles: (files: LocalUpload[]) => string | null | Promise<string | null>
  onDemo: (kind?: 'issues' | 'complete' | 'outlier') => void
  requirements: ReactNode
  usage: ReactNode
  search?: string; onSearch?: (query: string) => void; loading?: boolean
  onLoadMore?: () => void; loadingMore?: boolean
  refreshing?: boolean; errors?: string[]; onRefresh?: () => void
}) {
  const [localQuery, setLocalQuery] = useState('')
  const query = search ?? localQuery
  const setQuery = onSearch ?? setLocalQuery
  const [view, setView] = useState(routeView)
  const [queue, setQueue] = useState(routeQueue)
  function viewHref(next: typeof view) {
    const url = new URL(location.href); url.searchParams.set('view', next)
    return `${url.pathname}${url.search}${url.hash}`
  }
  function navigate(event: MouseEvent<HTMLAnchorElement>, next: typeof view) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    setView(next)
    history.pushState(null, '', viewHref(next))
    if (next === 'workbench') onRefresh?.()
    window.scrollTo({top: 0})
  }
  useEffect(() => {
    const sync = () => {
      const nextQueue = routeQueue()
      if (new URLSearchParams(location.search).get('view') === 'cases') {
        const url = new URL(location.href); url.searchParams.set('view', 'workbench'); url.searchParams.set('queue', 'all')
        history.replaceState(null, '', url)
      }
      setView(routeView()); setQueue(nextQueue)
    }
    sync()
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const shown = useMemo(() => onSearch ? items : items.filter(item => `${item.title} ${item.context} ${item.state} ${item.files?.map(file => file.name).join(' ') ?? ''}`.toLowerCase().includes(query.trim().toLowerCase())), [items, onSearch, query])
  const attention = useMemo(() => shown.filter(item => ['attention', 'prepare'].includes(workTask(item))), [shown])
  const review = useMemo(() => shown.filter(item => workTask(item) === 'review'), [shown])
  const activeQueue = queue ?? (attention.length ? 'attention' : review.length ? 'review' : 'all')
  useEffect(() => {if (!loading && queue === null) setQueue(current => current ?? activeQueue)}, [loading, queue, activeQueue])
  function changeQueue(value: string) {
    if (value !== 'attention' && value !== 'review' && value !== 'all' || value === queue) return
    setQueue(value)
    const url = new URL(location.href); url.searchParams.set('queue', value); history.replaceState(null, '', url)
  }
  const toolbar = <>
    <div role="search" className="flex min-w-0 flex-1 items-center gap-2 text-muted-foreground max-sm:basis-full sm:max-w-sm"><Search size={16} aria-hidden="true"/><Input type="search" aria-label="搜索零件、供应商或文件" placeholder="搜索零件、供应商或文件" value={query} maxLength={200} onChange={event => setQuery(event.target.value)}/>{query && <Button variant="ghost" size="sm" onClick={() => setQuery('')}>清除</Button>}</div>
    {onRefresh && <Button variant="outline" size="icon" aria-label="刷新审核记录" title="刷新审核记录" disabled={refreshing || loadingMore} onClick={onRefresh}><RefreshCw className={refreshing ? 'animate-spin motion-reduce:animate-none' : undefined}/></Button>}
  </>
  const table = (rows: WorkItem[], label: string) => <WorkItemsTable items={rows} label={label} onOpen={onOpen} loading={loading} readFailed={errors.length > 0} hasMore={Boolean(onLoadMore)} onLoadMore={onLoadMore} busy={loadingMore || refreshing} toolbar={toolbar}/>

  const upload = useLocalUploads(async files => {
    const error = await onFiles(files)
    if (error) setUploadError(error)
    else setUploadOpen(false)
  }, 'new-case')
  function pick(files: File[]) {setUploadError(''); void upload.upload(files)}
  const nav = <nav aria-label="主要导航">
    <Button asChild className="justify-start text-left max-md:flex-1 max-md:justify-center" variant={view === 'workbench' ? 'secondary' : 'ghost'}><a href={viewHref('workbench')} aria-current={view === 'workbench' ? 'page' : undefined} onClick={event => navigate(event, 'workbench')}><LayoutDashboard/><span>工作台</span></a></Button>
    <Button asChild className="justify-start text-left max-md:flex-1 max-md:justify-center" variant={view === 'requirements' ? 'secondary' : 'ghost'}><a href={viewHref('requirements')} aria-current={view === 'requirements' ? 'page' : undefined} onClick={event => navigate(event, 'requirements')}><BookOpen/><span>审核要求</span></a></Button>
  </nav>
  return <div className={"flex min-h-dvh bg-background text-foreground text-base"}>
    <a href="#task-main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:p-3 focus:outline-2 focus:outline-ring">跳到主要内容</a>
    <aside className={"sticky top-0 flex flex-[0_0_216px] flex-col h-dvh pt-7 pr-4 pb-4.5 pl-4 border-r bg-background [&_nav]:grid [&_nav]:gap-1.5 max-md:hidden"}>
      <div className={"flex items-center gap-2.5 pt-0 pr-3 pb-9 pl-3 text-xl [&_svg]:text-primary"}><ClipboardCheck aria-hidden="true"/><strong>PPAP</strong></div>
      {nav}
      <div className="mt-auto border-t pt-4"><AccountMenu/></div>
    </aside>
    <div className={"flex flex-1 flex-col min-w-0"}>
      <main id="task-main" tabIndex={-1} className={"w-full max-w-300 mx-auto pt-10 px-6 pb-15 max-md:pt-6.5 max-md:pr-5 max-md:pb-10 max-md:pl-5"}>
        {isPreview() && <Badge variant="outline" className="mb-4">交互预览</Badge>}
        {view === 'usage' ? usage : view === 'requirements' ? requirements : <>
        <header className="mb-6 flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">工作台</h1><Button onClick={() => {setUploadError(''); setUploadOpen(true)}}><UploadCloud/>上传资料</Button></header>
        {errors.length > 0 && <Alert className="mb-6"><AlertTitle>{shown.length ? '部分状态未能更新' : '暂时无法读取审核记录'}</AlertTitle><AlertDescription><div className="space-y-2">{errors.map(error => <p key={error}>{error}</p>)}{shown.length > 0 && <p>已读取的记录仍可打开；状态可能已变化。</p>}{onRefresh && <Button variant="outline" size="sm" disabled={refreshing || loadingMore} onClick={onRefresh}>{refreshing ? '正在读取…' : '重新读取'}</Button>}</div></AlertDescription></Alert>}
          <Tabs value={activeQueue} onValueChange={changeQueue}>
            <TabsList aria-label="审核记录"><TabsTrigger value="attention" onClick={() => changeQueue('attention')}>需要处理<Badge variant="secondary">{loading || errors.length > 0 && !attention.length ? '—' : `${attention.length}${onLoadMore ? '+' : ''}`}</Badge></TabsTrigger><TabsTrigger value="review" onClick={() => changeQueue('review')}>待复核<Badge variant="secondary">{loading || errors.length > 0 && !review.length ? '—' : `${review.length}${onLoadMore ? '+' : ''}`}</Badge></TabsTrigger><TabsTrigger value="all" onClick={() => changeQueue('all')}>全部记录</TabsTrigger></TabsList>
            <TabsContent key={activeQueue} value={activeQueue} className="mt-3">{table(activeQueue === 'attention' ? attention : activeQueue === 'review' ? review : shown, {attention: '需要处理', review: '待复核', all: '全部'}[activeQueue])}</TabsContent>
          </Tabs>
        {isPreview() && <Collapsible className={""}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">示例资料</Button></CollapsibleTrigger><CollapsibleContent><div className={"flex flex-wrap gap-2 my-3 mx-0"}><Button variant="outline" onClick={() => onDemo('issues')}>使用 P-204 示例资料包<ArrowRight/></Button><Button variant="outline" onClick={() => onDemo('complete')}>完整资料 · 可批准路径<ArrowRight/></Button><Button variant="outline" onClick={() => onDemo('outlier')}>尺寸超差 · 不批准路径<ArrowRight/></Button></div></CollapsibleContent></Collapsible>}
        </>}
      </main>
      <div className={"max-md:[&_nav_svg]:w-4.5 max-md:[&_nav_svg]:h-4.5 hidden max-md:sticky max-md:bottom-0 max-md:flex max-md:items-center max-md:gap-2 max-md:mt-auto max-md:pt-0 max-md:pr-4  max-md:pl-0 max-md:border-t max-md:bg-background max-md:[&_nav]:flex max-md:[&_nav]:flex-1"}>{nav}<AccountMenu compact/></div>

    </div>
    <Dialog open={uploadOpen} onOpenChange={open => {if (!open) upload.cancel(); setUploadOpen(open)}}>
      <DialogContent className={""}>
        <DialogHeader><DialogTitle>上传资料</DialogTitle><DialogDescription>先选择文件，之后核对零件、修订和归属。</DialogDescription></DialogHeader>
        <div className={"grid justify-items-center gap-3 py-7.5 px-5 border border-dashed rounded-lg text-center [&[data-dragging=\"true\"]]:bg-muted [&_>_span]:text-muted-foreground [&_>_span]:text-sm"} data-dragging={dragging} onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); pick(Array.from(event.dataTransfer.files))}}>
          <UploadCloud size={32} aria-hidden="true"/><strong>拖入收到的资料</strong><span>Excel、Word、PDF、图片或文本 · 单份最大 200 MB</span><Button disabled={upload.busy} onClick={() => input.current?.click()}>选择文件</Button><input ref={input} className={"sr-only"} aria-label="上传文件" type="file" multiple accept={fileAccept} disabled={upload.busy} onChange={event => {pick(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
        </div>
        {(uploadError || upload.error) && <p role="alert" className={"text-foreground text-sm"}>{uploadError || upload.error}</p>}
        <p className={"text-muted-foreground text-sm m-0"} role="status">{upload.busy ? '正在上传并保存原件…' : ''}</p>
      </DialogContent>
    </Dialog>
  </div>
}
