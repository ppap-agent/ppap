import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Same Stitch reader: pages are rendered locally, never uploaded to a conversion service.
import {useEffect, useRef, useState} from 'react'
import {ChevronLeft, ChevronRight, Minus, Plus, RotateCw} from 'lucide-react'
import {getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Input} from '../components/ui/input'
import {pdfSelection} from './pdf-selection'

GlobalWorkerOptions.workerSrc = workerUrl
const assetRoot = import.meta.env.DEV ? '/node_modules/pdfjs-dist' : '/pdfjs'
const assets = (folder: string) => new URL(`${assetRoot}/${folder}/`, location.origin).href

export function PdfSource({url, name, onCite}: {url: string; name: string; onCite?: (page: number, region?: number[]) => void}) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null), [error, setError] = useState('')
  const [page, setPage] = useState(1), [zoom, setZoom] = useState(1), [rotation, setRotation] = useState(0)
  const [width, setWidth] = useState(0), [rendering, setRendering] = useState(true), [text, setText] = useState('')
  const [region, setRegion] = useState<number[] | null>(null)
  const selection = useRef<{canvas: HTMLCanvasElement; view: ReturnType<import('pdfjs-dist').PDFPageProxy['getViewport']>; original: ReturnType<import('pdfjs-dist').PDFPageProxy['getViewport']>; start?: [number, number]; overlay?: HTMLDivElement} | null>(null)
  const viewport = useRef<HTMLDivElement>(null), paper = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let current = true, task: ReturnType<typeof getDocument> | undefined
    void Promise.resolve().then(() => {
      if (!current) return
      task = getDocument({url, cMapUrl: assets('cmaps'), cMapPacked: true, standardFontDataUrl: assets('standard_fonts'), wasmUrl: assets('wasm'), stopAtErrors: true})
      return task.promise
    }).then(value => {if (current && value) setPdf(value)}).catch(cause => {
      if (current) setError(cause?.name === 'PasswordException' ? '此 PDF 需要密码。请下载原件，在本机应用解锁后查看。' : '此 PDF 无法排版。请下载原件核对，或返回后补充可读版本。')
    })
    return () => {current = false; void task?.destroy().catch(() => {})}
  }, [url])
  useEffect(() => {
    if (!viewport.current) return
    const observer = new ResizeObserver(entries => setWidth(Math.floor(entries[0].contentRect.width)))
    observer.observe(viewport.current)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (!pdf || !width || !paper.current) return
    let current = true, task: RenderTask | undefined
    const host = paper.current
    selection.current = null; setRegion(null)
    host.replaceChildren(); setRendering(true); setError(''); setText('')
    void pdf.getPage(page).then(async sheet => {
      if (!current) return
      const base = sheet.getViewport({scale: 1, rotation: sheet.rotate + rotation})
      const view = sheet.getViewport({scale: Math.max(100, width - 24) / base.width * zoom, rotation: sheet.rotate + rotation})
      const ratio = Math.min(devicePixelRatio || 1, 2)
      const canvas = document.createElement('canvas')
      canvas.width = Math.ceil(view.width * ratio); canvas.height = Math.ceil(view.height * ratio)
      canvas.style.width = `${view.width}px`; canvas.style.height = `${view.height}px`
      canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `${name} · 第 ${page} 页`)
      task = sheet.render({canvas, viewport: view, transform: [ratio, 0, 0, ratio, 0, 0]})
      await task.promise
      if (!current) return
      selection.current = {canvas, view, original: sheet.getViewport({scale: 1, rotation: 0})}
      host.replaceChildren(canvas); viewport.current?.scrollTo(0, 0); setRendering(false)
      // This is embedded PDF text, not OCR or a statement of document correctness.
      try {
        const content = await sheet.getTextContent()
        if (current) setText(content.items.map(item => 'str' in item ? item.str + (item.hasEOL ? '\n' : ' ') : '').join(''))
      } catch {if (current) setText('')}
    }).catch(() => {if (current) {setRendering(false); setError(`第 ${page} 页无法排版。可以查看其他页，或下载原件核对。`)}})
    return () => {current = false; task?.cancel(); host.replaceChildren()}
  }, [pdf, page, width, zoom, rotation, name])
  function point(clientX: number, clientY: number): [number, number] | null {
    const value = selection.current
    if (!value) return null
    const bounds = value.canvas.getBoundingClientRect()
    return [(clientX - bounds.left) * value.view.width / bounds.width, (clientY - bounds.top) * value.view.height / bounds.height]
  }
  function clearRegion() {selection.current?.overlay?.remove(); if (selection.current) delete selection.current.overlay; setRegion(null)}
  function drawRegion(end: [number, number]) {
    const value = selection.current
    if (!value?.start || !value.overlay) return
    const x = [value.start[0], end[0]].map(point => Math.max(0, Math.min(value.view.width, point)))
    const y = [value.start[1], end[1]].map(point => Math.max(0, Math.min(value.view.height, point)))
    Object.assign(value.overlay.style, {left: `${value.canvas.offsetLeft + Math.min(...x)}px`, top: `${value.canvas.offsetTop + Math.min(...y)}px`, width: `${Math.abs(x[1] - x[0])}px`, height: `${Math.abs(y[1] - y[0])}px`})
  }
  return <div className={"flex flex-col h-full min-h-95"}>
    <div className={"flex shrink-0 flex-wrap gap-1 py-1 px-2 border-b max-md:gap-0"} role="toolbar" aria-label="PDF 阅读工具">
      <Button variant="ghost" size="icon-sm" aria-label="上一页" disabled={!pdf || page === 1} onClick={() => setPage(value => value - 1)}><ChevronLeft/></Button>
      <label className={"flex items-center gap-1 whitespace-nowrap text-xs"}>第 <Input aria-label="PDF 页码" type="number" min={1} max={pdf?.numPages ?? 1} value={page} disabled={!pdf} onChange={event => {const next = Number(event.target.value); if (pdf && Number.isInteger(next) && next >= 1 && next <= pdf.numPages) setPage(next)}}/> / {pdf?.numPages ?? '—'} 页</label>
      <Button variant="ghost" size="icon-sm" aria-label="下一页" disabled={!pdf || page >= pdf.numPages} onClick={() => setPage(value => value + 1)}><ChevronRight/></Button>
      <Button variant="ghost" size="icon-sm" aria-label="缩小 PDF" disabled={zoom <= .5} onClick={() => setZoom(value => Math.max(.5, value - .25))}><Minus/></Button>
      <Button variant="ghost" size="sm" onClick={() => setZoom(1)}>{zoom === 1 ? '适合宽度' : `适宽 × ${zoom}`}</Button>
      <Button variant="ghost" size="icon-sm" aria-label="放大 PDF" disabled={zoom >= 3} onClick={() => setZoom(value => Math.min(3, value + .25))}><Plus/></Button>
      <Button variant="ghost" size="icon-sm" aria-label="旋转 PDF" onClick={() => setRotation(value => (value + 90) % 360)}><RotateCw/></Button>
    </div>
    {onCite && <div className="flex flex-wrap items-center gap-2 px-2 py-1"><p className="text-xs text-muted-foreground">可拖选页面区域，或引用当前整页。</p><Button variant="outline" size="sm" disabled={rendering || Boolean(error)} onClick={() => onCite(page, region || undefined)}>{region ? '引用选区' : '引用当前页'}</Button>{region && <><span role="status" className="text-xs">已选择第 {page} 页区域</span><Button variant="ghost" size="sm" onClick={clearRegion}>清除选区</Button></>}</div>}
    <div className={"relative flex-1 min-h-40 overflow-auto"} ref={viewport} role="region" aria-label="PDF 页面" tabIndex={0} aria-busy={!error && rendering}>
      {error ? <p role="alert" className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"}>{error}</p> : rendering && <ReadSkeleton label={`正在排版第 ${page} 页…`} kind="document" className="min-h-55 p-3"/>}
      <div className={["relative w-max min-w-full p-3 [&_canvas]:block [&_canvas]:m-auto [&_canvas]:bg-background", onCite ? "touch-none cursor-crosshair" : ""].join(' ')} ref={paper} onPointerDown={event => {
        if (!onCite || event.button !== 0 || event.target !== selection.current?.canvas) return
        const start = point(event.clientX, event.clientY)
        if (start && selection.current) {event.preventDefault(); clearRegion(); selection.current.start = start; const overlay = document.createElement('div'); overlay.className = 'pointer-events-none absolute border-2 border-primary bg-primary/10'; overlay.setAttribute('aria-hidden', 'true'); selection.current.overlay = overlay; event.currentTarget.append(overlay); drawRegion(start); event.currentTarget.setPointerCapture(event.pointerId)}
      }} onPointerMove={event => {const end = point(event.clientX, event.clientY); if (end) drawRegion(end)}} onPointerUp={event => {
        const value = selection.current, end = point(event.clientX, event.clientY)
        if (!onCite || !value?.start || !end) return
        const next = pdfSelection(value.start, end, value.view, value.original); setRegion(next); if (!next) value.overlay?.remove(); drawRegion(end); delete value.start
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
      }} onPointerCancel={() => {if (selection.current) delete selection.current.start; clearRegion()}}/>
    </div>
    {pdf && !rendering && !error && <Collapsible className={"max-h-40 overflow-auto"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">本页文字（可复制）</Button></CollapsibleTrigger><CollapsibleContent><pre>{text.trim() || '此页没有可提取的文字，请查看页面图像。'}</pre></CollapsibleContent></Collapsible>}
  </div>
}
