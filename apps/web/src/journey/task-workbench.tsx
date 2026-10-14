import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Reference: Stitch projects/13261964020862199279/screens/582261af90b5486790ed77a3e764d67d.
import {useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowRight, BookOpen, ClipboardCheck, Files, LayoutDashboard, Search, UploadCloud} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {AccountMenu, isPreview} from './access'
import {fileAccept, useLocalUploads, type LocalUpload} from './local-files'

export type WorkItem = {
  id: string
  title: string
  context: string
  state: string
  next: string
  detail: string
  received: boolean
  updatedAt: string
}

const routeView = () => new URLSearchParams(location.search).get('view') === 'usage' ? 'usage' : new URLSearchParams(location.search).get('view') === 'requirements' ? 'requirements' : new URLSearchParams(location.search).get('view') === 'cases' ? 'cases' : 'workbench'
export function TaskWorkbench({items, onOpen, onFiles, onDemo, requirements, usage}: {
  items: WorkItem[]
  onOpen: (id: string) => void
  onFiles: (files: LocalUpload[]) => string | null | Promise<string | null>
  onDemo: (kind?: 'issues' | 'complete' | 'outlier') => void
  requirements: ReactNode
  usage: ReactNode
}) {
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'workbench' | 'cases' | 'requirements' | 'usage'>(routeView)
  function navigate(next: typeof view) {
    setView(next)
    const url = new URL(location.href); url.searchParams.set('view', next); history.pushState(null, '', url)
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
  const shown = items.filter(item => `${item.title} ${item.context} ${item.state}`.toLowerCase().includes(query.trim().toLowerCase()))
  const groups = view === 'cases' ? [{title: '全部事项与待核对资料', items: shown}] : [
    {title: '待核对资料', items: shown.filter(item => !item.received)},
    {title: '继续处理', items: shown.filter(item => item.received)},
  ]
  const upload = useLocalUploads(async files => {
    const error = await onFiles(files)
    if (error) setUploadError(error)
    else setUploadOpen(false)
  }, 'new-case')
  function pick(files: File[]) {setUploadError(''); void upload.upload(files)}
  const nav = <nav aria-label="主要导航">
    <Button variant={view === 'workbench' ? 'secondary' : 'ghost'} aria-current={view === 'workbench' ? 'page' : undefined} onClick={() => navigate('workbench')}><LayoutDashboard/><span>工作台</span></Button>
    <Button variant={view === 'cases' ? 'secondary' : 'ghost'} aria-current={view === 'cases' ? 'page' : undefined} onClick={() => navigate('cases')}><Files/><span>事项</span></Button>
    <Button variant={view === 'requirements' ? 'secondary' : 'ghost'} aria-current={view === 'requirements' ? 'page' : undefined} onClick={() => navigate('requirements')}><BookOpen/><span>审核要求</span></Button>
  </nav>
  return <div className={"flex min-h-dvh bg-background text-foreground text-base"}>
    <aside className={"sticky top-0 flex flex-[0_0_216px] flex-col h-dvh pt-7 pr-4 pb-4.5 pl-4 border-r bg-background [&_nav]:grid [&_nav]:gap-1.5 max-md:hidden"}>
      <div className={"flex items-center gap-2.5 pt-0 pr-3 pb-9 pl-3 text-xl [&_svg]:text-primary"}><ClipboardCheck aria-hidden="true"/><strong>PPAP</strong></div>
      {nav}
      <div className={"flex items-center gap-2.5 mt-auto pt-4.5 pr-2 pb-0 pl-2 border-t"}><AccountMenu/></div>
    </aside>
    <div className={"flex flex-1 flex-col min-w-0"}>
      <main id="task-main" className={"w-full max-w-300 mx-auto pt-10 px-6 pb-15 px-6 max-md:pt-6.5 max-md:pr-5 max-md:pb-10 max-md:pl-5"}>
        {view === 'usage' ? usage : view === 'requirements' ? requirements : <>
        <header className={"flex items-center justify-between gap-5 mb-7 [&_h1]:m-0 [&_h1]:text-3xl [&_h1]:font-semibold [&_h1]:tracking-tight max-md:mb-5.5 max-md:[&_h1]:text-2xl"}><h1>{view === 'workbench' ? '工作台' : '事项'}</h1><Button className={""} onClick={() => {setUploadError(''); setUploadOpen(true)}}><UploadCloud/>上传资料</Button></header>
        <div className={"flex items-center gap-2.5 w-full max-w-140 mb-6 text-muted-foreground [&_>_svg]:shrink-0"}><Search size={18} aria-hidden="true"/><Input aria-label="搜索零件、供应商或事项" placeholder="搜索零件、供应商或事项" value={query} onChange={event => setQuery(event.target.value)}/>{query && <Button variant="ghost" onClick={() => setQuery('')}>清除</Button>}</div>
        {groups.map(group => group.items.length > 0 && <section className={"mb-8"} key={group.title} aria-label={group.title}>
          <div className={"flex items-baseline gap-2.5 mb-3 [&_h2]:m-0 [&_h2]:text-base [&_h2]:font-semibold [&_>_span]:text-muted-foreground [&_>_span]:text-sm [&_>_span]:tabular-nums"}><h2>{group.title}</h2><span>{group.items.length}</span></div>
          <div className={"border-t"}>{group.items.map(item => <article className={"flex items-center justify-between gap-6 py-5.5 px-0 border-b max-md:items-start max-md:flex-col max-md:gap-3.5 max-md:py-5"} key={item.id}>
            <div className={"min-w-0 [&_p]:mt-2 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:break-all [&_p]:leading-normal"}><div className={"flex items-center flex-wrap gap-2.5 [&_h3]:m-0 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:break-all"}><h3>{item.title}</h3><span className={"py-1 px-2 rounded-lg bg-muted text-primary text-xs whitespace-nowrap"}>{item.state}</span></div><p>{item.context}</p><p className={""}>{item.detail}</p></div>
            <div className={"flex shrink-0 items-end flex-col gap-2.5 [&_time]:text-muted-foreground [&_time]:text-xs [&_time]:tabular-nums max-md:items-center max-md:justify-between max-md:w-full"}><Button variant="outline" onClick={() => onOpen(item.id)}>{item.next}<ArrowRight/></Button><time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false})}</time></div>
          </article>)}</div>
        </section>)}
        {!shown.length && <section className={"grid justify-items-center pt-11 pr-0 pb-13 pl-0 text-center [&_>_svg]:mb-4 [&_>_svg]:text-muted-foreground [&_h2]:m-0 [&_h2]:text-xl [&_h2]:font-semibold [&_p]:max-w-100 [&_p]:mt-2.5 [&_p]:mr-0 [&_p]:mb-5 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm"}><Files size={30} aria-hidden="true"/><h2>{query ? '没有找到匹配的事项' : '从收到的资料开始'}</h2><p>{query ? '试试零件号或供应商名称。' : '先上传文件，再核对它属于哪个事项、使用哪个版本。'}</p>{query ? <Button variant="outline" onClick={() => setQuery('')}>清除搜索</Button> : <Button variant="outline" onClick={() => setUploadOpen(true)}>选择文件</Button>}</section>}
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
        <p className={"text-muted-foreground text-sm m-0"} role="status">{upload.busy ? '正在上传并保存原件…' : '选择文件后核对资料归属。'}</p>
      </DialogContent>
    </Dialog>
  </div>
}
