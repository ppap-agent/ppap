// Stitch: projects/13261964020862199279/screens/133b2c862eb7461a8d247b6341396709.
// Three structures inside the current task: document, comparison, and clause-led reading.
// Read-only: closing, zooming, downloading and locating never save a review judgment.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, Download, LocateFixed, Minus, Plus, RotateCcw, X} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger} from '../src/components/ui/dialog'
import {DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../src/components/ui/dropdown-menu'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../src/components/ui/select'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../src/components/ui/tabs'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../src/components/ui/table'
import type {DecisionEvidence, DecisionInput} from './decision'
import {documentRows, sourceDocumentHTML, sourceKey, sourceVersionLabel, type DemoDocument, type SourceFile} from './source-documents'
import './source-reader.css'
import {LocalSourcePane} from './local-source-pane'

type ReadingInput = Pick<DecisionInput, 'receiptId' | 'scope' | 'files' | 'evidence' | 'clarification' | 'receipts'>
type Props = {input: ReadingInput; documents?: DemoDocument[]; clauseId?: string; file?: SourceFile; label?: string}
const layouts = ['document', 'comparison', 'clauses']
const labels = ['原件优先', '双文件对照', '逐条证据']
function scopeLabel(scope: string) {
  try {
    const value: unknown = JSON.parse(scope)
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const fields = value as Record<string, unknown>
      if (['customer', 'supplier', 'part', 'revision', 'plant'].every(key => typeof fields[key] === 'string'))
        return [fields.customer, fields.supplier, fields.part, `图纸 Rev ${fields.revision}`, fields.plant].filter(Boolean).join(' · ')
    }
  } catch { /* Older snapshots can store an ordinary display label. */ }
  return scope || '当前资料'
}

function SourceSelect({label, value, onChange, files, current}: {label: string; value: string; onChange: (value: string) => void; files: SourceFile[]; current: SourceFile[]}) {
  return <Select value={value} onValueChange={onChange}><SelectTrigger aria-label={label} className="gf-reader-select"><SelectValue placeholder="选择文件"/></SelectTrigger><SelectContent>{files.map((file, index) => <SelectItem key={sourceKey(file)} value={sourceKey(file)}>{file.name} · {file.localFile ? ['未解析', '未读取', '未知', '待核对'].includes(file.revision) ? '修订待核对' : `登记修订 ${file.revision}` : `Rev ${file.revision}`} · {sourceVersionLabel(file)}{files.filter(item => item.name === file.name && item.revision === file.revision && item.sourceVersion === file.sourceVersion).length > 1 ? ` · 第 ${index + 1} 份` : ''}{current.some(item => sourceKey(item) === sourceKey(file)) ? '' : ' · 历史'}</SelectItem>)}</SelectContent></Select>
}

function DocumentPane({file, documents, row, current, onClose, side}: {file?: SourceFile; documents: DemoDocument[]; row?: DecisionEvidence; current: SourceFile[]; onClose: () => void; side: string}) {
  const candidates = documents.filter(doc => file && sourceKey(doc) === sourceKey(file))
  const doc = candidates.length === 1 ? candidates[0] : null
  const [page, setPage] = useState('content'), [zoom, setZoom] = useState(100), [highlight, setHighlight] = useState(true)
  const [status, setStatus] = useState<'ready' | 'failed' | 'loading'>('ready')
  const [notice, setNotice] = useState('')
  const viewport = useRef<HTMLDivElement>(null)
  const isCurrent = file && current.some(item => sourceKey(item) === sourceKey(file))
  const unambiguous = file && current.filter(item => item.name === file.name).length === 1
  const sources = isCurrent && unambiguous ? row?.sources.filter(source => source.file === file?.name) ?? [] : []
  const locatedKeys = sources.length && doc
    ? doc.kind === 'dimension' && row?.id === 'R3' ? documentRows(doc).map(item => item.key).filter(key => ['B2', 'D8', 'E8', 'F8'].includes(key))
      : doc.kind !== 'dimension' && row?.id === 'R1' ? ['17']
        : doc.kind === 'flow' && row?.id === 'R2' ? doc.readable === false ? ['unreadable'] : ['16', '17', '18'] : []
    : []
  useEffect(() => {
    if (status !== 'loading') return
    const timer = window.setTimeout(() => setStatus('ready'), 450)
    return () => window.clearTimeout(timer)
  }, [status])
  if (file?.localFile) return <LocalSourcePane file={{...file, localFile: file.localFile}} side={side}/>
  if (!doc) return <section className="gf-reader-pane" aria-label={`${side}阅读区`}>
    <div className="gf-reader-canvas"><div className="gf-reader-empty"><BookOpen/>
      <h3>{!file ? `选择${side === '对照' ? '对照' : '阅读'}文件` : candidates.length > 1 ? '来源版本有歧义' : '原件尚不可读取'}</h3>
      <p>{!file ? '从上方选择一份资料及其版本。' : candidates.length > 1 ? '同一文件版本对应多个候选来源，不能自动选择。' : '这份旧记录仅保存了文件名，无法核实当时内容。请返回后将原件作为新来件上传。'}</p>
      {file && <Button variant="outline" onClick={onClose}>返回当前工作</Button>}
    </div></div>
  </section>
  function locate() {
    if (!locatedKeys.length) {setNotice('此版本没有对应定位，请从原文核对。'); return}
    setPage('content'); setHighlight(true)
    window.requestAnimationFrame(() => viewport.current?.querySelector('[data-located=true]')?.scrollIntoView({block: 'center', behavior: 'smooth'}))
    setNotice('已定位当前条款。')
  }
  function download() {
    if (!doc) return
    const url = URL.createObjectURL(new Blob([sourceDocumentHTML(doc)], {type: 'text/html;charset=utf-8'}))
    const link = document.createElement('a'); link.href = url; link.download = `${doc.name}.示例.html`; link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    setNotice('已请求下载示例 HTML，不是原始 Excel/PDF。')
  }
  return <Tabs value={page} onValueChange={setPage} asChild><section className="gf-reader-pane" aria-label={`${side}阅读区`}>
    <div className="gf-reader-tools" role="toolbar" aria-label={`${side}阅读工具`}>
      <Button variant="ghost" size="sm" onClick={locate} disabled={!doc || status !== 'ready'}><LocateFixed/>定位证据</Button>
      <Button variant="ghost" size="icon-sm" aria-label={`${side}缩小`} disabled={zoom <= 75 || !doc} onClick={() => setZoom(zoom - 25)}><Minus/></Button>
      <Button variant="ghost" size="sm" aria-label={`${side}重置缩放`} disabled={!doc} onClick={() => setZoom(100)}>{zoom}%</Button>
      <Button variant="ghost" size="icon-sm" aria-label={`${side}放大`} disabled={zoom >= 200 || !doc} onClick={() => setZoom(zoom + 25)}><Plus/></Button>
      <Button variant="ghost" size="sm" disabled={!doc || status !== 'ready'} onClick={download}><Download/>下载示例</Button>
    </div>
    {doc && <TabsList variant="line" aria-label={`${side}文档内容`}><TabsTrigger value="content">{doc.kind === 'flow' ? '第 3 页 · 工序' : doc.kind === 'control' ? '工作表 · 控制计划' : '工作表 · 尺寸'}</TabsTrigger><TabsTrigger value="identity">资料标识</TabsTrigger></TabsList>}
    <TabsContent value="content" forceMount hidden={page !== 'content'} className="gf-reader-canvas" ref={viewport} tabIndex={0} aria-label={`${side}可滚动文档`}>
      {status !== 'ready' ? <div className="gf-reader-empty" role="status"><h3>{status === 'loading' ? '正在重新载入…' : '原件载入失败'}</h3><p>文件版本与复核草稿已保留。</p>{status === 'failed' && <Button variant="outline" onClick={() => setStatus('loading')}><RotateCcw/>重试载入</Button>}</div> : <article className="gf-reader-paper" style={{fontSize: `${14 * zoom / 100}px`}}>
        <div className="gf-reader-paper-heading"><strong>支架 P-204</strong><small>Rev {doc.revision} · {sourceVersionLabel(doc)}</small></div>
        <><Table><TableHeader><TableRow><TableHead>位置</TableHead><TableHead>{doc.kind === 'dimension' ? '项目' : '工序号'}</TableHead><TableHead>{doc.kind === 'dimension' ? '值' : '工序名称'}</TableHead></TableRow></TableHeader><TableBody>{documentRows(doc).map(item => <TableRow key={item.key} data-located={highlight && locatedKeys.includes(item.key) ? 'true' : undefined}>{item.cells.map((cell, index) => <TableCell key={index}>{cell}</TableCell>)}</TableRow>)}</TableBody></Table>
          {doc.readable === false && <p className="gf-reader-unreadable">一处工序号不可读，需清晰件。</p>}</>
      </article>}
    </TabsContent>
    <TabsContent value="identity" forceMount hidden={page !== 'identity'} className="gf-reader-canvas">{doc && <article className="gf-reader-paper"><h3>资料标识</h3><dl><dt>资料名称</dt><dd>{doc.name}</dd><dt>内容修订</dt><dd>Rev {doc.revision} · {sourceVersionLabel(doc)}</dd><dt>来源批次</dt><dd>{doc.origin}</dd><dt>内容范围</dt><dd>此示例定义的标识、工序或尺寸记录。</dd></dl></article>}</TabsContent>
    <footer><span role="status">{notice || (doc ? `${doc.origin} · 只读` : '')}</span>{doc && <DropdownMenu><DropdownMenuTrigger asChild><Button size="sm" variant="ghost" aria-label={`${side}阅读设置`}>阅读设置</Button></DropdownMenuTrigger><DropdownMenuContent side="top" align="end"><DropdownMenuCheckboxItem checked={highlight} onCheckedChange={setHighlight}>定位高亮</DropdownMenuCheckboxItem>{import.meta.env.DEV && <DropdownMenuItem onSelect={() => {setPage('content'); setStatus('failed')}}>模拟载入失败</DropdownMenuItem>}</DropdownMenuContent></DropdownMenu>}</footer>
  </section></Tabs>
}

function Reader({input, documents = [], clauseId, file, onClose}: Props & {onClose: () => void}) {
  const files = [...new Map([...input.files, ...(input.receipts?.flatMap(item => item.files) ?? [])].map(item => [sourceKey(item), item])).values()]
  const [active, setActive] = useState(clauseId ?? input.evidence[0]?.id ?? '')
  const row = input.evidence.find(item => item.id === active)
  function evidenceFile(name?: string) {
    const matches = input.files.filter(item => item.name === name)
    return matches.length === 1 ? matches[0] : undefined
  }
  const onlyFile = files.length === 1 ? files[0] : undefined
  // A sole original is safe to open, but ambiguous machine sources still need selection.
  const first = file ?? (row ? row.sources.length ? evidenceFile(row.sources[0]?.file) : onlyFile : files[0])
  const [left, setLeft] = useState(first ? sourceKey(first) : '')
  const [right, setRight] = useState(() => {const other = row ? evidenceFile(row.sources[1]?.file) : undefined; return other && sourceKey(other) !== left ? sourceKey(other) : ''})
  const [layout, setLayout] = useState(() => Math.max(0, layouts.indexOf(new URLSearchParams(location.search).get('readerLayout') ?? 'document')))
  const [mobileSide, setMobileSide] = useState('left')
  function selectLayout(index: number) {setLayout(index); const url = new URL(location.href); url.searchParams.set('readerLayout', layouts[index]); history.replaceState(null, '', url)}
  function selectClause(id: string) {setActive(id); const next = input.evidence.find(item => item.id === id); const a = next?.sources.length ? evidenceFile(next.sources[0]?.file) : onlyFile, b = evidenceFile(next?.sources[1]?.file); setLeft(a ? sourceKey(a) : ''); setRight(b && (!a || sourceKey(a) !== sourceKey(b)) ? sourceKey(b) : ''); setMobileSide('left')}
  const context = <aside className="gf-reader-context" aria-label="要求与证据摘录"><h3>{row ? `${row.id} · ${row.title}` : '阅读资料'}</h3>{row && <><p>{row.requirement}</p><p><strong>机器检查：{row.result}</strong></p><p>{row.explanation}</p><h4>机器摘录</h4>{row.sources.length ? row.sources.map((source, index) => <div key={index} className="gf-reader-quote"><strong>{source.file}</strong><small>{source.location}</small><pre>{source.excerpt}</pre><Button size="sm" variant="ghost" onClick={() => {const candidates = input.files.filter(item => item.name === source.file); if (candidates.length === 1) {setLeft(sourceKey(candidates[0])); setMobileSide('left')}}} disabled={input.files.filter(item => item.name === source.file).length !== 1}>打开此来源</Button></div>) : <p>没有可定位的证据摘录。查看文件不会补出检查结果。</p>}</>}{input.clarification && <details><summary>供应商原话</summary><pre>{input.clarification}</pre></details>}</aside>
  const pane = (side: 'left' | 'right') => <div className={`gf-reader-document gf-reader-document--${side}`} data-mobile-visible={mobileSide === side}><SourceSelect label={side === 'left' ? '主要阅读文件' : '对照阅读文件'} value={side === 'left' ? left : right} onChange={side === 'left' ? setLeft : setRight} files={files} current={input.files}/><DocumentPane key={`${side}:${side === 'left' ? left : right}`} side={side === 'left' ? '主要' : '对照'} file={files.find(item => sourceKey(item) === (side === 'left' ? left : right))} documents={documents} row={row} current={input.files} onClose={onClose}/></div>
  return <DialogContent className="gf-app gf-reader" showCloseButton={false} onKeyDown={event => {if (['ArrowLeft','ArrowRight'].includes(event.key) && !(event.target as HTMLElement).closest('input,textarea,select,button,[role=combobox],[role=tab]')) {event.preventDefault(); event.stopPropagation(); selectLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)}}}>
    <header className="gf-reader-heading"><div><DialogTitle>原件与证据</DialogTitle><DialogDescription>{scopeLabel(input.scope)} · {input.receiptId}</DialogDescription></div><Button aria-label="返回当前工作" variant="outline" onClick={onClose}><X/><span className="gf-reader-return-label">返回当前工作</span></Button></header>
    <div className="gf-reader-basis"><span>阅读不会改变审核判断。{files.some(item => item.localFile) ? '上传原件仅存本机。' : '当前为合成示例资料。'}</span>{input.evidence.length > 0 && <Select value={active} onValueChange={selectClause}><SelectTrigger aria-label="阅读关联条款"><SelectValue/></SelectTrigger><SelectContent>{input.evidence.map(item => <SelectItem key={item.id} value={item.id}>{item.id} · {item.title}</SelectItem>)}</SelectContent></Select>}</div>
    <div className={`gf-reader-body gf-reader-body--${layouts[layout]}${!row && !input.clarification ? ' gf-reader-body--no-context' : ''}`}>
      {layout === 2 && <nav className="gf-reader-clause-nav" aria-label="按条款阅读">{input.evidence.length ? input.evidence.map(item => <Button key={item.id} variant={active === item.id ? 'secondary' : 'ghost'} onClick={() => selectClause(item.id)}>{item.id} · {item.title}<small>{item.result}</small></Button>) : <p>本次尚无检查条款。</p>}</nav>}
      {layout === 1 ? <div className="gf-reader-comparison"><div className="gf-reader-mobile-tabs" role="group" aria-label="窄屏对照资料"><Button variant={mobileSide === 'left' ? 'secondary' : 'ghost'} aria-pressed={mobileSide === 'left'} onClick={() => setMobileSide('left')}>主要文件</Button><Button variant={mobileSide === 'right' ? 'secondary' : 'ghost'} aria-pressed={mobileSide === 'right'} onClick={() => setMobileSide('right')}>对照文件</Button></div>{pane('left')}{pane('right')}</div> : pane('left')}
      {(row || input.clarification) && (layout === 0 ? context : <details className="gf-reader-context-disclosure"><summary>要求、机器摘录与收到的说明</summary>{context}</details>)}
    </div>
    {import.meta.env.DEV && <div className="gf-reader-switcher" role="toolbar" aria-label="阅读布局"><Button variant="ghost" size="icon-sm" aria-label="上一种阅读布局" onClick={() => selectLayout((layout + 2) % 3)}><ArrowLeft/></Button><span>{labels[layout]}</span><Button variant="ghost" size="icon-sm" aria-label="下一种阅读布局" onClick={() => selectLayout((layout + 1) % 3)}><ArrowRight/></Button></div>}
  </DialogContent>
}

export function SourceReaderButton(props: Props) {
  const [open, setOpen] = useState(false)
  return <Dialog open={open} onOpenChange={setOpen}><DialogTrigger asChild><Button variant="outline" size="sm" aria-label={props.file ? `${props.label ?? '打开原件'} · ${props.file.name}` : undefined}><BookOpen/>{props.label ?? '打开原件与证据'}</Button></DialogTrigger>{open && <Reader {...props} onClose={() => setOpen(false)}/>}</Dialog>
}
