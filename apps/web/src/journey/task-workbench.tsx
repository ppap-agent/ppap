import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Reference: Stitch projects/13261964020862199279/screens/537ac169f04b40f587c5b59452a7ffba.
import {useEffect, useRef, useState, type MouseEvent, type ReactNode} from 'react'
import {ArrowRight, BookOpen, ClipboardCheck, Files, LayoutDashboard, RefreshCw, Search, UploadCloud} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {Input} from '../components/ui/input'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {AccountMenu, isPreview} from './access'
import {fileAccept, useLocalUploads, type LocalUpload} from './local-files'

export type WorkItem = {
  id: string
  href: string
  title: string
  context: string
  state: string
  next: string
  detail: string
  received: boolean
  updatedAt: string
  task?: 'attention' | 'review' | 'prepare' | 'running' | 'completed'
}

const routeView = () => new URLSearchParams(location.search).get('view') === 'usage' ? 'usage' : new URLSearchParams(location.search).get('view') === 'requirements' ? 'requirements' : new URLSearchParams(location.search).get('view') === 'cases' ? 'cases' : 'workbench'
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
  const [view, setView] = useState<'workbench' | 'cases' | 'requirements' | 'usage'>(routeView)
  function viewHref(next: typeof view) {
    const url = new URL(location.href); url.searchParams.set('view', next)
    return `${url.pathname}${url.search}${url.hash}`
  }
  function navigate(event: MouseEvent<HTMLAnchorElement>, next: typeof view) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    setView(next)
    history.pushState(null, '', viewHref(next))
    if (next === 'workbench' || next === 'cases') onRefresh?.()
    window.scrollTo({top: 0})
  }
  useEffect(() => {
    const sync = () => setView(routeView())
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const shown = onSearch ? items : items.filter(item => `${item.title} ${item.context} ${item.state}`.toLowerCase().includes(query.trim().toLowerCase()))
  const groupOf = (item: WorkItem) => item.task ?? (!item.received ? 'prepare' : 'review')
  const groups = view === 'cases' ? [{title: '全部事项', items: shown}] : [
    {title: '需要处理', items: shown.filter(item => groupOf(item) === 'attention')},
    {title: '待复核', items: shown.filter(item => groupOf(item) === 'review')},
    {title: '审核准备', items: shown.filter(item => groupOf(item) === 'prepare')},
    {title: '系统处理中', items: shown.filter(item => groupOf(item) === 'running')},
    ...(query ? [{title: '已复核', items: shown.filter(item => groupOf(item) === 'completed')}] : []),
  ]
  const completed = shown.filter(item => groupOf(item) === 'completed')

  const upload = useLocalUploads(async files => {
    const error = await onFiles(files)
    if (error) setUploadError(error)
    else setUploadOpen(false)
  }, 'new-case')
  function pick(files: File[]) {setUploadError(''); void upload.upload(files)}
  const nav = <nav aria-label="主要导航">
    <Button asChild className="justify-start text-left max-md:flex-1 max-md:justify-center" variant={view === 'workbench' ? 'secondary' : 'ghost'}><a href={viewHref('workbench')} aria-current={view === 'workbench' ? 'page' : undefined} onClick={event => navigate(event, 'workbench')}><LayoutDashboard/><span>工作台</span></a></Button>
    <Button asChild className="justify-start text-left max-md:flex-1 max-md:justify-center" variant={view === 'cases' ? 'secondary' : 'ghost'}><a href={viewHref('cases')} aria-current={view === 'cases' ? 'page' : undefined} onClick={event => navigate(event, 'cases')}><Files/><span>事项</span></a></Button>
    <Button asChild className="justify-start text-left max-md:flex-1 max-md:justify-center" variant={view === 'requirements' ? 'secondary' : 'ghost'}><a href={viewHref('requirements')} aria-current={view === 'requirements' ? 'page' : undefined} onClick={event => navigate(event, 'requirements')}><BookOpen/><span>审核要求</span></a></Button>
  </nav>
  return <div className={"flex min-h-dvh bg-background text-foreground text-base"}>
    <aside className={"sticky top-0 flex flex-[0_0_216px] flex-col h-dvh pt-7 pr-4 pb-4.5 pl-4 border-r bg-background [&_nav]:grid [&_nav]:gap-1.5 max-md:hidden"}>
      <div className={"flex items-center gap-2.5 pt-0 pr-3 pb-9 pl-3 text-xl [&_svg]:text-primary"}><ClipboardCheck aria-hidden="true"/><strong>PPAP</strong></div>
      {nav}
      <div className="mt-auto border-t pt-4"><AccountMenu/></div>
    </aside>
    <div className={"flex flex-1 flex-col min-w-0"}>
      <main id="task-main" className={"w-full max-w-300 mx-auto pt-10 px-6 pb-15 max-md:pt-6.5 max-md:pr-5 max-md:pb-10 max-md:pl-5"}>
        {view === 'usage' ? usage : view === 'requirements' ? requirements : <>
        <header className="mb-6 flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">{view === 'workbench' ? '工作台' : '事项'}</h1><Button onClick={() => {setUploadError(''); setUploadOpen(true)}}><UploadCloud/>上传资料</Button></header>
        <div className="mb-6 flex items-center gap-2.5"><div role="search" className="flex min-w-0 max-w-xl flex-1 items-center gap-2 text-muted-foreground"><Search size={18} aria-hidden="true"/><Input type="search" aria-label="搜索零件、供应商或事项" placeholder="搜索零件、供应商或事项" value={query} maxLength={200} onChange={event => setQuery(event.target.value)}/>{query && <Button variant="ghost" onClick={() => setQuery('')}>清除</Button>}</div>{onRefresh && <Button variant="outline" size="icon" aria-label="刷新事项" title="刷新事项" disabled={refreshing || loadingMore} onClick={onRefresh}><RefreshCw className={refreshing ? 'animate-spin motion-reduce:animate-none' : undefined}/></Button>}</div>
        {errors.length > 0 && <Alert className="mb-6"><AlertTitle>{shown.length ? '部分状态未能更新' : '暂时无法读取事项'}</AlertTitle><AlertDescription><div className="space-y-2">{errors.map(error => <p key={error}>{error}</p>)}{shown.length > 0 && <p>已读取的记录仍可打开；状态可能已变化。</p>}{onRefresh && <Button variant="outline" size="sm" disabled={refreshing || loadingMore} onClick={onRefresh}>{refreshing ? '正在读取…' : '重新读取'}</Button>}</div></AlertDescription></Alert>}
        {loading && <p role="status" className="mb-4 text-sm text-muted-foreground">正在读取事项…</p>}
        {groups.map(group => group.items.length > 0 && <section className={"mb-8"} key={group.title} aria-label={group.title}>
          <div className={"flex items-baseline gap-2.5 mb-3 [&_h2]:m-0 [&_h2]:text-base [&_h2]:font-semibold [&_>_span]:text-muted-foreground [&_>_span]:text-sm [&_>_span]:tabular-nums"}><h2>{group.title}</h2><span>{group.items.length}</span></div>
          <div className={"border-t"}>{group.items.map(item => <article className={"flex items-center justify-between gap-6 py-4 px-0 border-b max-md:items-start max-md:flex-col max-md:gap-2.5"} key={item.id}>
            <div className={"min-w-0 [&_p]:mt-2 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:break-words [&_p]:leading-normal"}><div className={"flex items-center flex-wrap gap-2.5 [&_h3]:m-0 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:break-all"}><h3>{item.title}</h3>{item.state !== group.title && <Badge variant="outline">{item.state}</Badge>}</div><p>{item.context}</p>{item.detail && <p>{item.detail}</p>}</div>
            <div className={"flex shrink-0 items-end flex-col gap-2 [&_time]:text-muted-foreground [&_time]:text-xs [&_time]:tabular-nums max-md:flex-row max-md:items-center max-md:justify-between max-md:w-full"}><Button variant="outline" asChild><a href={item.href} onClick={event => {if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); onOpen(item.href)}}>{item.next}<ArrowRight/></a></Button><time className="max-md:order-first" dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false})}</time></div>
          </article>)}</div>
        </section>)}
        {!loading && !errors.length && !shown.length && <section className={"grid justify-items-center pt-11 pr-0 pb-13 pl-0 text-center [&_>_svg]:mb-4 [&_>_svg]:text-muted-foreground [&_h2]:m-0 [&_h2]:text-xl [&_h2]:font-semibold [&_p]:max-w-100 [&_p]:mt-2.5 [&_p]:mr-0 [&_p]:mb-5 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm"}><Files size={30} aria-hidden="true"/><h2>{query ? '没有找到匹配的事项' : '从收到的资料开始'}</h2><p>{query ? '试试零件号或供应商名称。' : '先上传文件，再核对它属于哪个事项、使用哪个版本。'}</p>{query ? <Button variant="outline" onClick={() => setQuery('')}>清除搜索</Button> : <Button variant="outline" onClick={() => setUploadOpen(true)}>选择文件</Button>}</section>}
        {view === 'workbench' && !query && completed.length > 0 && <div className="flex flex-wrap items-center justify-between gap-3 border-t py-4"><span className="text-sm text-muted-foreground">当前列表已复核 {completed.length} 项</span><Button variant="ghost" asChild><a href={viewHref('cases')} onClick={event => navigate(event, 'cases')}>查看全部事项<ArrowRight/></a></Button></div>}
        {onLoadMore && <div className="flex justify-center py-4"><Button variant="outline" disabled={loadingMore || refreshing} onClick={onLoadMore}>{loadingMore ? '正在读取…' : '加载更多事项'}</Button></div>}
        {isPreview() && <Collapsible className={""}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">原型说明与示例</Button></CollapsibleTrigger><CollapsibleContent><p>未连接后端；文件与登记信息仅保存在此浏览器；清理网站数据会使它们丢失，请保留收到的原件。示例包可演示检查、澄清、复核与决定；任意本地文件不会得到预设审核结论。</p><div className={"flex flex-wrap gap-2 my-3 mx-0"}><Button variant="outline" onClick={() => onDemo('issues')}>使用 P-204 示例资料包<ArrowRight/></Button><Button variant="outline" onClick={() => onDemo('complete')}>完整资料 · 可批准路径<ArrowRight/></Button><Button variant="outline" onClick={() => onDemo('outlier')}>尺寸超差 · 不批准路径<ArrowRight/></Button></div></CollapsibleContent></Collapsible>}
        </>}
      </main>
      <div className={"max-md:[&_nav_svg]:w-4.5 max-md:[&_nav_svg]:h-4.5 hidden max-md:sticky max-md:bottom-0 max-md:flex max-md:items-center max-md:gap-2 max-md:mt-auto max-md:pt-0 max-md:pr-4  max-md:pl-0 max-md:border-t max-md:bg-background max-md:[&_nav]:flex max-md:[&_nav]:flex-1"}>{nav}<AccountMenu compact/></div>

    </div>
    <Dialog open={uploadOpen} onOpenChange={open => {if (!open) upload.cancel(); setUploadOpen(open)}}>
      <DialogContent className={""}>
        <DialogHeader><DialogTitle>上传资料</DialogTitle><DialogDescription>先选择文件，之后核对零件、修订和归属。</DialogDescription></DialogHeader>
        <div className={"grid justify-items-center gap-3 py-7.5 px-5 border border-dashed rounded-lg text-center [&[data-dragging=\"true\"]]:bg-muted [&_>_span]:text-muted-foreground [&_>_span]:text-sm"} data-dragging={dragging} onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); pick(Array.from(event.dataTransfer.files))}}>
          <UploadCloud size={32} aria-hidden="true"/><strong>拖入收到的资料</strong><span>Excel、Word、PDF、图片或文本 · 单份最大 64 MB</span><Button disabled={upload.busy} onClick={() => input.current?.click()}>选择文件</Button><input ref={input} className={"sr-only"} aria-label="上传文件" type="file" multiple accept={fileAccept} disabled={upload.busy} onChange={event => {pick(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
        </div>
        {(uploadError || upload.error) && <p role="alert" className={"text-foreground text-sm"}>{uploadError || upload.error}</p>}
        <p className={"text-muted-foreground text-sm m-0"} role="status">{upload.busy ? '正在上传并保存原件…' : ''}</p>
      </DialogContent>
    </Dialog>
  </div>
}
