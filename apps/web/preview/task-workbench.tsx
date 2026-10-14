// Reference: Stitch projects/13261964020862199279/screens/582261af90b5486790ed77a3e764d67d.
import {useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowRight, BookOpen, ClipboardCheck, Files, LayoutDashboard, Search, UploadCloud} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Input} from '../src/components/ui/input'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../src/components/ui/dialog'
import {PrototypeAccount} from './access'
import './task-workbench.css'
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
  onFiles: (files: LocalUpload[]) => string | null
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
  const upload = useLocalUploads(files => {
    const error = onFiles(files)
    if (error) setUploadError(error)
    else setUploadOpen(false)
  }, 'new-case')
  function pick(files: File[]) {setUploadError(''); void upload.upload(files)}
  const nav = <nav aria-label="主要导航">
    <Button variant="ghost" aria-current={view === 'workbench' ? 'page' : undefined} onClick={() => navigate('workbench')}><LayoutDashboard/><span>工作台</span></Button>
    <Button variant="ghost" aria-current={view === 'cases' ? 'page' : undefined} onClick={() => navigate('cases')}><Files/><span>事项</span></Button>
    <Button variant="ghost" aria-current={view === 'requirements' ? 'page' : undefined} onClick={() => navigate('requirements')}><BookOpen/><span>审核要求</span></Button>
  </nav>
  return <div className="gf-app gf-task-app">
    <aside className="gf-task-sidebar">
      <div className="gf-task-brand"><ClipboardCheck aria-hidden="true"/><strong>PPAP</strong></div>
      {nav}
      <div className="gf-task-account"><PrototypeAccount/></div>
    </aside>
    <div className="gf-task-workspace">
      <main id="task-main" className="gf-task-main">
        {view === 'usage' ? usage : view === 'requirements' ? requirements : <>
        <header className="gf-task-heading"><h1>{view === 'workbench' ? '工作台' : '事项'}</h1><Button className="gf-primary" onClick={() => {setUploadError(''); setUploadOpen(true)}}><UploadCloud/>上传资料</Button></header>
        <div className="gf-task-search"><Search size={18} aria-hidden="true"/><Input aria-label="搜索零件、供应商或事项" placeholder="搜索零件、供应商或事项" value={query} onChange={event => setQuery(event.target.value)}/>{query && <Button variant="ghost" onClick={() => setQuery('')}>清除</Button>}</div>
        {groups.map(group => group.items.length > 0 && <section className="gf-task-section" key={group.title} aria-label={group.title}>
          <div className="gf-task-section-head"><h2>{group.title}</h2><span>{group.items.length}</span></div>
          <div className="gf-task-list">{group.items.map(item => <article className="gf-task-row" key={item.id}>
            <div className="gf-task-copy"><div className="gf-task-row-title"><h3>{item.title}</h3><span className="gf-task-status">{item.state}</span></div><p>{item.context}</p><p className="gf-task-next">{item.detail}</p></div>
            <div className="gf-task-row-action"><Button variant="outline" onClick={() => onOpen(item.id)}>{item.next}<ArrowRight/></Button><time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false})}</time></div>
          </article>)}</div>
        </section>)}
        {!shown.length && <section className="gf-task-empty"><Files size={30} aria-hidden="true"/><h2>{query ? '没有找到匹配的事项' : '从收到的资料开始'}</h2><p>{query ? '试试零件号或供应商名称。' : '先上传文件，再核对它属于哪个事项、使用哪个版本。'}</p>{query ? <Button variant="outline" onClick={() => setQuery('')}>清除搜索</Button> : <Button variant="outline" onClick={() => setUploadOpen(true)}>选择文件</Button>}</section>}
        <details className="gf-task-demo"><summary>原型说明与示例</summary><p>未连接后端；文件与登记信息仅保存在此浏览器；清理网站数据会使它们丢失，请保留收到的原件。示例包可演示检查、澄清、复核与决定；任意本地文件不会得到预设审核结论。</p><div className="gf-dec-downloads"><Button variant="outline" onClick={() => onDemo('issues')}>使用 P-204 示例资料包<ArrowRight/></Button><Button variant="outline" onClick={() => onDemo('complete')}>完整资料 · 可批准路径<ArrowRight/></Button><Button variant="outline" onClick={() => onDemo('outlier')}>尺寸超差 · 不批准路径<ArrowRight/></Button></div></details>
        </>}
      </main>
      <div className="gf-task-mobile-nav">{nav}<PrototypeAccount compact/></div>
      <footer className="gf-task-footer">交互原型 · 仅本机记录</footer>
    </div>
    <Dialog open={uploadOpen} onOpenChange={open => {if (!open) upload.cancel(); setUploadOpen(open)}}>
      <DialogContent className="gf-task-upload-dialog">
        <DialogHeader><DialogTitle>上传资料</DialogTitle><DialogDescription>先选择文件，之后核对零件、修订和归属。</DialogDescription></DialogHeader>
        <div className="gf-task-dropzone" data-dragging={dragging} onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); pick(Array.from(event.dataTransfer.files))}}>
          <UploadCloud size={32} aria-hidden="true"/><strong>拖入收到的资料</strong><span>Excel、Word、PDF、图片或文本 · 单份最大 64 MB</span><Button disabled={upload.busy} onClick={() => input.current?.click()}>选择文件</Button><input ref={input} className="sr-only" aria-label="上传文件" type="file" multiple accept={fileAccept} disabled={upload.busy} onChange={event => {pick(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
        </div>
        {(uploadError || upload.error) && <p role="alert" className="gf-task-error">{uploadError || upload.error}</p>}
        <p className="gf-task-local-note" role="status">{upload.busy ? '正在保存本机原件…' : '保存完成后核对归属；取消不会创建事项。'}</p>
      </DialogContent>
    </Dialog>
  </div>
}
