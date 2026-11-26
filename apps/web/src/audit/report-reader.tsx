// Report reader: the cover and ten chapters on the left, the fixed PDF on the right. Pages render locally with pdf.js.
import {useEffect, useRef, useState, type CSSProperties, type FormEvent, type RefObject} from 'react'
import {iconAnglesLeft, iconAnglesRight, iconMagnifyingGlass, iconMinus, iconPlus} from '../components/icons'
import {Icon} from '../components/ui/icon'
import {getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ReadSkeleton} from '../components/read-skeleton'
import {chapterAt, outlineChapters, searchOrder, textIncludes, type ReportChapter} from './report-chapters'
import {narrowNow} from '../components/narrow'

GlobalWorkerOptions.workerSrc = workerUrl
const assetRoot = import.meta.env.DEV ? '/node_modules/pdfjs-dist' : '/pdfjs'
const assets = (folder: string) => new URL(`${assetRoot}/${folder}/`, location.origin).href

/** `target` moves the reader to an outline entry (0 is the cover); a new nonce repeats the same move. */
export function ReportReader({url, name, kind, target}: {url: string; name: string; kind: string; target?: {index: number; nonce: number} | null}) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null), [error, setError] = useState('')
  const [chapters, setChapters] = useState<ReportChapter[]>([]), [ratio, setRatio] = useState(842 / 595)
  const [open, setOpen] = useState(() => !narrowNow()), [page, setPage] = useState(1), [zoom, setZoom] = useState(1), [width, setWidth] = useState(0)
  const [query, setQuery] = useState(''), [found, setFound] = useState('')
  const scroller = useRef<HTMLDivElement>(null), texts = useRef(new Map<number, string>())
  useEffect(() => {
    let current = true
    const task = getDocument({url, cMapUrl: assets('cmaps'), cMapPacked: true, standardFontDataUrl: assets('standard_fonts'), wasmUrl: assets('wasm'), stopAtErrors: true})
    texts.current = new Map()
    task.promise.then(async value => {
      const first = await value.getPage(1), view = first.getViewport({scale: 1})
      const entries = await outlineChapters(await value.getOutline(), async dest => {
        const explicit = typeof dest === 'string' ? await value.getDestination(dest) : dest
        const ref = explicit?.[0]
        if (ref == null) throw new Error('no_destination')
        return typeof ref === 'number' ? ref + 1 : await value.getPageIndex(ref as Parameters<PDFDocumentProxy['getPageIndex']>[0]) + 1
      })
      if (current) {setRatio(view.height / view.width); setChapters(entries); setPdf(value); setPage(1)}
    }).catch(cause => {if (current) setError(cause?.name === 'PasswordException' ? '此 PDF 需要密码，请下载后在本机查看。' : '此 PDF 无法排版，请下载报告查看。')})
    return () => {current = false; void task.destroy().catch(() => {})}
  }, [url])
  useEffect(() => {
    const element = scroller.current
    if (!element) return
    const observer = new ResizeObserver(entries => setWidth(Math.floor(entries[0].contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [pdf])
  const pageWidth = Math.max(200, Math.min(width - 32, 900) * zoom)
  function pageElement(number: number) {return scroller.current?.querySelector<HTMLElement>(`[data-page="${number}"]`) ?? null}
  function goToPage(number: number) {
    const element = pageElement(number), host = scroller.current
    if (element && host) {host.scrollTo({top: element.offsetTop - 16}); setPage(number)}
  }
  function readPosition() {
    const host = scroller.current
    if (!host || !pdf) return
    // The page reaching the upper third of the view is the page being read.
    const line = host.scrollTop + host.clientHeight / 3
    let current = 1
    for (let number = 1; number <= pdf.numPages; number += 1) {const element = pageElement(number); if (element && element.offsetTop <= line) current = number}
    setPage(current)
  }
  useEffect(() => {
    if (!pdf || !target || !width) return
    const chapter = chapters[target.index]
    // A report without an outline (an older report) opens at its first page.
    requestAnimationFrame(() => goToPage(chapter?.page ?? 1))
  }, [pdf, target?.index, target?.nonce, chapters, width])
  async function find(event: FormEvent) {
    event.preventDefault()
    if (!pdf || !query.trim()) return
    setFound('正在查找…')
    for (const number of searchOrder(page, pdf.numPages)) {
      let text = texts.current.get(number)
      if (text === undefined) {
        try {const content = await (await pdf.getPage(number)).getTextContent(); text = content.items.map(item => 'str' in item ? item.str : '').join('')} catch {text = ''}
        texts.current.set(number, text)
      }
      if (textIncludes(text, query)) {goToPage(number); setFound(`在第 ${number} 页`); return}
    }
    setFound('未找到')
  }
  const current = chapterAt(chapters, page)
  if (error) return <div className="grid min-h-55 place-content-center gap-3 p-6"><h3 className="text-lg font-semibold">PDF 无法显示</h3><p role="alert" className="text-muted-foreground">{error}</p></div>
  if (!pdf) return <ReadSkeleton label="正在加载报告…" kind="document" className="min-h-96 p-4"/>
  // Below 1280px the contents open over the page as a drawer and close once a chapter is chosen.
  return <div className="relative flex h-full min-h-0 min-w-0">
    {chapters.length > 0 && open && <nav aria-label="报告目录" className="flex w-56 shrink-0 flex-col border-r bg-card max-xl:absolute max-xl:inset-y-0 max-xl:left-0 max-xl:z-20 max-xl:w-64 max-xl:shadow-lg">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2"><span className="text-sm font-semibold">目录</span><Button variant="ghost" size="xs" onClick={() => setOpen(false)}><Icon icon={iconAnglesLeft} aria-hidden="true"/>收起目录</Button></div>
      <ol className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2 text-sm">{chapters.map((chapter, index) => <li key={`${index}-${chapter.title}`}>
        <button type="button" aria-current={index === current ? 'location' : undefined} title={chapter.title} onClick={() => {goToPage(chapter.page); if (narrowNow()) setOpen(false)}} className="w-full truncate rounded-sm px-2 py-1.5 text-left text-muted-foreground hover:bg-muted aria-[current=location]:bg-primary-soft aria-[current=location]:font-medium aria-[current=location]:text-primary focus-visible:outline-ring">{chapter.title}</button>
      </li>)}</ol>
    </nav>}
    <div className="flex min-w-0 flex-1 flex-col">
      <div role="toolbar" aria-label="PDF 阅读工具" className="flex shrink-0 flex-wrap items-center gap-1 border-b px-2 py-1">
        {chapters.length > 0 && !open && <Button variant="ghost" size="sm" onClick={() => setOpen(true)}><Icon icon={iconAnglesRight} aria-hidden="true"/>展开目录（{chapters.length}）</Button>}
        <Badge shape="square" variant="outline">{kind}</Badge>
        <label className="flex items-center gap-1 whitespace-nowrap text-xs">第 <Input aria-label="PDF 页码" className="h-7 w-14" type="number" min={1} max={pdf.numPages} value={page} onChange={event => {const next = Number(event.target.value); if (Number.isInteger(next) && next >= 1 && next <= pdf.numPages) goToPage(next)}}/> / {pdf.numPages} 页</label>
        <Button variant="ghost" size="icon-sm" aria-label="缩小 PDF" disabled={zoom <= .5} onClick={() => setZoom(value => Math.max(.5, value - .25))}><Icon icon={iconMinus}/></Button>
        <Button variant="ghost" size="sm" onClick={() => setZoom(1)}>{zoom === 1 ? '适合宽度' : `适宽 × ${zoom}`}</Button>
        <Button variant="ghost" size="icon-sm" aria-label="放大 PDF" disabled={zoom >= 3} onClick={() => setZoom(value => Math.min(3, value + .25))}><Icon icon={iconPlus}/></Button>
        <form role="search" aria-label="在报告中查找" className="ml-auto flex items-center gap-1" onSubmit={event => void find(event)}>
          <Input aria-label="查找内容" className="h-7 w-36" value={query} placeholder="查找" onChange={event => {setQuery(event.target.value); setFound('')}}/>
          <Button type="submit" variant="ghost" size="icon-sm" aria-label="查找下一处" disabled={!query.trim()}><Icon icon={iconMagnifyingGlass}/></Button>
          <span role="status" className="min-w-12 text-xs text-muted-foreground">{found}</span>
        </form>
      </div>
      <div ref={scroller} role="region" aria-label="PDF 页面" tabIndex={0} onScroll={readPosition} className="relative min-h-40 flex-1 space-y-4 overflow-auto bg-muted p-4">
        {width > 0 && Array.from({length: pdf.numPages}, (_, index) => <ReportPage key={index + 1} pdf={pdf} number={index + 1} width={pageWidth} ratio={ratio} name={name} root={scroller}/>)}
      </div>
    </div>
  </div>
}

/** One page, drawn when it comes near the view and again when its width changes. */
function ReportPage({pdf, number, width, ratio, name, root}: {pdf: PDFDocumentProxy; number: number; width: number; ratio: number; name: string; root: RefObject<HTMLDivElement | null>}) {
  const host = useRef<HTMLDivElement>(null), [near, setNear] = useState(false), [state, setState] = useState<'waiting' | 'drawn' | 'failed'>('waiting')
  useEffect(() => {
    const element = host.current
    if (!element) return
    const observer = new IntersectionObserver(entries => {if (entries.some(entry => entry.isIntersecting)) setNear(true)}, {root: root.current, rootMargin: '150% 0px'})
    observer.observe(element)
    return () => observer.disconnect()
  }, [root])
  useEffect(() => {
    if (!near || !host.current) return
    let current = true, task: RenderTask | undefined
    const element = host.current
    void pdf.getPage(number).then(async sheet => {
      const base = sheet.getViewport({scale: 1}), view = sheet.getViewport({scale: width / base.width})
      const scale = Math.min(devicePixelRatio || 1, 2)
      const canvas = document.createElement('canvas')
      canvas.width = Math.ceil(view.width * scale); canvas.height = Math.ceil(view.height * scale)
      canvas.style.width = `${view.width}px`; canvas.style.height = `${view.height}px`
      canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `${name} · 第 ${number} 页`)
      task = sheet.render({canvas, viewport: view, transform: [scale, 0, 0, scale, 0, 0]})
      await task.promise
      if (current) {element.replaceChildren(canvas); setState('drawn')}
    }).catch(() => {if (current) setState('failed')})
    return () => {current = false; task?.cancel()}
  }, [near, pdf, number, width, name])
  return <div data-page={number} className="mx-auto w-(--page-width) min-h-(--page-height) bg-card shadow-md" style={{'--page-width': `${width}px`, '--page-height': `${width * ratio}px`} as CSSProperties}>
    {state !== 'drawn' && <p className="p-6 text-xs text-muted-foreground">{state === 'failed' ? `第 ${number} 页无法排版，可以查看其他页或下载报告。` : `正在排版第 ${number} 页…`}</p>}
    {/* The canvas is placed here outside React. */}
    <div ref={host}/>
  </div>
}
