import {isPreview} from './access'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch: projects/13261964020862199279/screens/133b2c862eb7461a8d247b6341396709.
// Three structures inside the current task: document, comparison, and clause-led reading.
// Read-only: closing, zooming, downloading and locating never save a review judgment.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, Download, LocateFixed, Minus, Plus, RotateCcw, X} from 'lucide-react'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../components/ui/dropdown-menu'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import type {DecisionEvidence, DecisionInput} from './decision'
import {documentRows, sourceDocumentHTML, sourceKey, sourceVersionLabel, type DemoDocument, type SourceFile, type SourceFocus} from './source-documents'
import {LocalSourcePane} from './local-source-pane'
import {RemoteSourcePane} from './remote-source'
import {FileIcon, FileName} from './file-name'

type ReadingInput = Pick<DecisionInput, 'receiptId' | 'scope' | 'files' | 'evidence' | 'clarification' | 'receipts'>
type Props = {input: ReadingInput; documents?: DemoDocument[]; clauseId?: string; file?: SourceFile; label?: string; className?: string; variant?: 'outline' | 'ghost' | 'link'}
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
  return <Select value={value} onValueChange={onChange}><SelectTrigger aria-label={label} className="w-full min-w-0"><SelectValue className="min-w-0" placeholder="选择文件"/></SelectTrigger><SelectContent className="max-w-[calc(100vw-2rem)]">{files.map((file, index) => <SelectItem key={sourceKey(file)} value={sourceKey(file)} textValue={file.name}><FileIcon name={file.name}/><span className="min-w-0 whitespace-normal [overflow-wrap:anywhere]">{file.name}{file.material ? '' : ' · '}{file.material ? '' : file.localFile ? ['未解析', '未读取', '未知', '待确认'].includes(file.revision) ? '修订待确认' : `登记修订 ${file.revision}` : `Rev ${file.revision}`} · {sourceVersionLabel(file)}{files.filter(item => item.name === file.name).length > 1 ? file.material ? ` · 上传版本 ${file.material.file_version_id}` : ` · 第 ${index + 1} 份` : ''}{current.some(item => sourceKey(item) === sourceKey(file)) ? '' : ' · 历史'}</span></SelectItem>)}</SelectContent></Select>
}

export function DocumentPane({file, documents, row, current, onClose, side, focus}: {file?: SourceFile; documents: DemoDocument[]; row?: DecisionEvidence; current: SourceFile[]; onClose: () => void; side: string; focus?: SourceFocus}) {
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
  if (file?.material) return <RemoteSourcePane file={file.material} roundId={file.roundId} focus={focus}/>
  if (file?.localFile) return <LocalSourcePane file={{...file, localFile: file.localFile}} side={side}/>
  if (!doc) return <section className={"flex flex-col min-h-0 flex-1 bg-background border [&_>_[hidden]]:hidden [&_>_footer]:flex [&_>_footer]:shrink-0 [&_>_footer]:flex-wrap [&_>_footer]:items-center [&_>_footer]:gap-2.5 [&_>_footer]:py-2.5 [&_>_footer]:px-3 [&_>_footer]:text-xs [&_>_footer]:text-muted-foreground [&_>_footer]:border-t [&_>_footer_>_span]:flex-1"} aria-label={`${side}阅读区`}>
    <div className={"flex-1 min-h-45 overflow-auto p-5.5 bg-background  max-md:min-h-0 "}><div className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"}><BookOpen/>
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
  return <Tabs value={page} onValueChange={setPage} asChild><section className={"flex flex-col min-h-0 flex-1 bg-background border [&_>_[hidden]]:hidden [&_>_footer]:flex [&_>_footer]:shrink-0 [&_>_footer]:flex-wrap [&_>_footer]:items-center [&_>_footer]:gap-2.5 [&_>_footer]:py-2.5 [&_>_footer]:px-3 [&_>_footer]:text-xs [&_>_footer]:text-muted-foreground [&_>_footer]:border-t [&_>_footer_>_span]:flex-1"} aria-label={`${side}阅读区`}>
    <div className={"flex shrink-0 flex-wrap gap-1 py-1 px-2 border-b max-md:gap-0"} role="toolbar" aria-label={`${side}阅读工具`}>
      <Button variant="ghost" size="sm" onClick={locate} disabled={!doc || status !== 'ready'}><LocateFixed/>定位证据</Button>
      <Button variant="ghost" size="icon-sm" aria-label={`${side}缩小`} disabled={zoom <= 75 || !doc} onClick={() => setZoom(zoom - 25)}><Minus/></Button>
      <Button variant="ghost" size="sm" aria-label={`${side}重置缩放`} disabled={!doc} onClick={() => setZoom(100)}>{zoom}%</Button>
      <Button variant="ghost" size="icon-sm" aria-label={`${side}放大`} disabled={zoom >= 200 || !doc} onClick={() => setZoom(zoom + 25)}><Plus/></Button>
      <Button variant="ghost" size="sm" disabled={!doc || status !== 'ready'} onClick={download}><Download/>下载示例</Button>
    </div>
    {doc && <TabsList variant="line" aria-label={`${side}文档内容`}><TabsTrigger value="content">{doc.kind === 'flow' ? '第 3 页 · 工序' : doc.kind === 'control' ? '工作表 · 控制计划' : '工作表 · 尺寸'}</TabsTrigger><TabsTrigger value="identity">资料标识</TabsTrigger></TabsList>}
    <TabsContent value="content" forceMount hidden={page !== 'content'} className={"flex-1 min-h-45 overflow-auto  max-md:min-h-0 "} ref={viewport} tabIndex={0} aria-label={`${side}可滚动文档`}>
      {status === 'loading' ? <ReadSkeleton label="正在重新载入…" kind="document" className="min-h-55 p-3"/> : status === 'failed' ? <div className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"} role="status"><h3>原件载入失败</h3><p>文件版本与复核草稿已保留。</p><Button variant="outline" onClick={() => setStatus('loading')}><RotateCcw/>重试载入</Button></div> : <article className={"[zoom:var(--source-scale)] bg-background text-foreground border p-5.5 min-h-full [&_table]:text-sm [&_th]:text-sm [&_th]:leading-relaxed [&_th]:whitespace-normal [&_th]:border [&_th]:p-3 [&_td]:text-sm [&_td]:leading-relaxed [&_td]:whitespace-normal [&_td]:border [&_td]:p-3 [&_th]:bg-background [&_th]:text-muted-foreground [&_tr[data-located=true]]:bg-muted [&_dl]:grid [&_dl]:grid-cols-[auto_1fr] [&_dl]:gap-3.5 [&_dd]:m-0 [&_dd]:break-all [&_dt]:text-muted-foreground max-md:p-3 max-md:[&_th]:p-2 max-md:[&_th]:min-w-16 max-md:[&_td]:p-2 max-md:[&_td]:min-w-16"} style={{'--source-scale': zoom / 100} as React.CSSProperties}>
        <div className={"flex flex-wrap items-baseline gap-y-2.5 gap-x-5 border-b pb-3.5 mb-6.5 [&_span]:text-xs [&_span]:text-muted-foreground [&_strong]:text-xs"}><strong>支架 P-204</strong><small>Rev {doc.revision} · {sourceVersionLabel(doc)}</small></div>
        <><Table><TableHeader><TableRow><TableHead>位置</TableHead><TableHead>{doc.kind === 'dimension' ? '项目' : '工序号'}</TableHead><TableHead>{doc.kind === 'dimension' ? '值' : '工序名称'}</TableHead></TableRow></TableHeader><TableBody>{documentRows(doc).map(item => <TableRow key={item.key} data-located={highlight && locatedKeys.includes(item.key) ? 'true' : undefined}>{item.cells.map((cell, index) => <TableCell key={index}>{cell}</TableCell>)}</TableRow>)}</TableBody></Table>
          {doc.readable === false && <p className={"p-3 mt-5 bg-muted text-foreground"}>一处工序号不可读，需清晰件。</p>}</>
      </article>}
    </TabsContent>
    <TabsContent value="identity" forceMount hidden={page !== 'identity'} className={"flex-1 min-h-45 overflow-auto  max-md:min-h-0 "}>{doc && <article className={"[zoom:var(--source-scale)] bg-background text-foreground border p-5.5 min-h-full [&_table]:text-sm [&_th]:text-sm [&_th]:leading-relaxed [&_th]:whitespace-normal [&_th]:border [&_th]:p-3 [&_td]:text-sm [&_td]:leading-relaxed [&_td]:whitespace-normal [&_td]:border [&_td]:p-3 [&_th]:bg-background [&_th]:text-muted-foreground [&_tr[data-located=true]]:bg-muted [&_dl]:grid [&_dl]:grid-cols-[auto_1fr] [&_dl]:gap-3.5 [&_dd]:m-0 [&_dd]:break-all [&_dt]:text-muted-foreground max-md:p-3 max-md:[&_th]:p-2 max-md:[&_th]:min-w-16 max-md:[&_td]:p-2 max-md:[&_td]:min-w-16"}><h3>资料标识</h3><dl><dt>资料名称</dt><dd>{doc.name}</dd><dt>内容修订</dt><dd>Rev {doc.revision} · {sourceVersionLabel(doc)}</dd><dt>来源批次</dt><dd>{doc.origin}</dd><dt>内容范围</dt><dd>此示例定义的标识、工序或尺寸记录。</dd></dl></article>}</TabsContent>
    <footer><span role="status">{notice || (doc ? `${doc.origin} · 只读` : '')}</span>{doc && <DropdownMenu><DropdownMenuTrigger asChild><Button size="sm" variant="ghost" aria-label={`${side}阅读设置`}>阅读设置</Button></DropdownMenuTrigger><DropdownMenuContent side="top" align="end"><DropdownMenuCheckboxItem checked={highlight} onCheckedChange={setHighlight}>定位高亮</DropdownMenuCheckboxItem>{isPreview() && import.meta.env.DEV && <DropdownMenuItem onSelect={() => {setPage('content'); setStatus('failed')}}>模拟载入失败</DropdownMenuItem>}</DropdownMenuContent></DropdownMenu>}</footer>
  </section></Tabs>
}

function Reader({input, documents = [], clauseId, file, onClose, onCloseAutoFocus}: Props & {onClose: () => void; onCloseAutoFocus?: (event: Event) => void}) {
  const files = [...new Map([...input.files, ...(input.receipts?.flatMap(item => item.files) ?? [])].map(item => [sourceKey(item), item])).values()]
  const [active, setActive] = useState(clauseId ?? input.evidence[0]?.id ?? '')
  const row = input.evidence.find(item => item.id === active)
  function evidenceFile(name?: string, version?: string) {
    const matches = input.files.filter(item => version ? item.material?.file_version_id === version : item.name === name)
    return matches.length === 1 ? matches[0] : undefined
  }
  const onlyFile = files.length === 1 ? files[0] : undefined
  // A sole original is safe to open, but ambiguous machine sources still need selection.
  const first = file ?? (row ? row.sources.length ? evidenceFile(row.sources[0]?.file, row.sources[0]?.fileVersionId) : onlyFile : files[0])
  const [left, setLeft] = useState(first ? sourceKey(first) : '')
  const [right, setRight] = useState(() => {const other = row ? evidenceFile(row.sources[1]?.file, row.sources[1]?.fileVersionId) : undefined; return other && sourceKey(other) !== left ? sourceKey(other) : ''})
  const [layout, setLayout] = useState(() => Math.max(0, layouts.indexOf(new URLSearchParams(location.search).get('readerLayout') ?? 'document')))
  const [mobileSide, setMobileSide] = useState('left')
  function selectLayout(index: number) {setLayout(index); const url = new URL(location.href); url.searchParams.set('readerLayout', layouts[index]); history.replaceState(null, '', url)}
  function selectClause(id: string) {setActive(id); const next = input.evidence.find(item => item.id === id); const a = next?.sources.length ? evidenceFile(next.sources[0]?.file, next.sources[0]?.fileVersionId) : onlyFile, b = evidenceFile(next?.sources[1]?.file, next?.sources[1]?.fileVersionId); setLeft(a ? sourceKey(a) : ''); setRight(b && (!a || sourceKey(a) !== sourceKey(b)) ? sourceKey(b) : ''); setMobileSide('left')}
  const context = <aside className={"overflow-auto min-w-0 text-sm pr-1 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:mt-0 [&_h3]:mr-0 [&_h3]:mb-3 [&_h3]:ml-0 [&_h4]:font-semibold [&_h4]:mt-6 [&_p]:my-2.5 [&_p]:mx-0 [&_p]:leading-relaxed"} aria-label="要求与证据摘录"><h3>{row ? `${row.displayId || row.id} · ${row.title}` : '阅读资料'}</h3>{row && <><p>{row.requirement}</p><p><strong>机器检查：{row.result}</strong></p><p>{row.explanation}</p><h4>机器摘录</h4>{row.sources.length ? row.sources.map((source, index) => <div key={index} className={"my-3.5 py-2.5 px-0 border-b [&_strong]:block [&_strong]:break-all [&_small]:block [&_small]:break-all [&_small]:text-muted-foreground [&_small]:my-1.5"}><strong><FileName name={source.file}/></strong><small>{source.location}</small><pre>{source.excerpt}</pre><Button size="sm" variant="ghost" onClick={() => {const candidates = input.files.filter(item => item.name === source.file); if (candidates.length === 1) {setLeft(sourceKey(candidates[0])); setMobileSide('left')}}} disabled={input.files.filter(item => item.name === source.file).length !== 1}>打开此来源</Button></div>) : <p>没有可定位的证据摘录。查看文件不会补出检查结果。</p>}</>}{input.clarification && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">供应商原话</Button></CollapsibleTrigger><CollapsibleContent><pre>{input.clarification}</pre></CollapsibleContent></Collapsible>}</aside>
  const pane = (side: 'left' | 'right') => <div className={["flex flex-col min-w-0 min-h-0 gap-2",({} as Record<string,string>)[String(side)] ?? "",""].filter(Boolean).join("")} data-mobile-visible={mobileSide === side}><SourceSelect label={side === 'left' ? '主要阅读文件' : '对照阅读文件'} value={side === 'left' ? left : right} onChange={side === 'left' ? setLeft : setRight} files={files} current={input.files}/><DocumentPane key={`${side}:${side === 'left' ? left : right}`} side={side === 'left' ? '主要' : '对照'} file={files.find(item => sourceKey(item) === (side === 'left' ? left : right))} documents={documents} row={row} current={input.files} onClose={onClose}/></div>
  return <DialogContent className={"flex h-dvh w-screen max-w-none flex-col sm:h-[90dvh] sm:max-w-7xl"} showCloseButton={false} onCloseAutoFocus={onCloseAutoFocus} onKeyDown={event => {if (isPreview() && import.meta.env.DEV && ['ArrowLeft','ArrowRight'].includes(event.key) && !(event.target as HTMLElement).closest('input,textarea,select,button,[role=combobox],[role=tab]')) {event.preventDefault(); event.stopPropagation(); selectLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)}}}>
    <header className={"flex justify-between items-center gap-4 [&_>_div]:min-w-0 max-md:items-start"}><div><DialogTitle>原件与证据</DialogTitle><DialogDescription>{scopeLabel(input.scope)} · {input.receiptId}</DialogDescription></div><Button aria-label="返回当前工作" variant="outline" onClick={onClose}><X/><span className={"max-md:hidden"}>返回当前工作</span></Button></header>
    <div className={"flex justify-between gap-4.5 items-center text-muted-foreground text-xs [&_[role=combobox]]:w-[min(380px,_55%)] max-md:flex-col max-md:items-stretch max-md:gap-2 max-md:[&_[role=combobox]]:w-full"}><span>只读原件</span>{input.evidence.length > 0 && <Select value={active} onValueChange={selectClause}><SelectTrigger className="w-full min-w-0 max-w-full" aria-label="阅读关联条款"><SelectValue className="min-w-0"/></SelectTrigger><SelectContent>{input.evidence.map(item => <SelectItem key={item.id} value={item.id}>{item.displayId || item.id} · {item.title}</SelectItem>)}</SelectContent></Select>}</div>
    <div className={["flex-1 grid min-h-0 min-w-0 gap-5 overflow-auto",({"document":"grid-cols-[minmax(0,_1fr)_300px] max-lg:grid-cols-[minmax(0,_1fr)]","comparison":"grid-cols-[minmax(0,_1fr)]","clauses":"grid-cols-[215px_minmax(0,_1fr)] max-lg:grid-cols-[170px_minmax(0,_1fr)] max-md:grid-cols-[minmax(0,_1fr)]"} as Record<string,string>)[String(layouts[layout])] ?? "","",!row && !input.clarification ? "" : "",""].filter(Boolean).join("")}>
      {layout === 2 && <nav className={"flex flex-col gap-2 overflow-auto [&_small]:text-muted-foreground max-md:flex-row max-md:overflow-auto max-md:max-h-24"} aria-label="按条款阅读">{input.evidence.length ? input.evidence.map(item => <Button key={item.id} variant={active === item.id ? 'secondary' : 'ghost'} onClick={() => selectClause(item.id)}>{item.displayId || item.id} · {item.title}<small>{item.result}</small></Button>) : <p>本次尚无检查条款。</p>}</nav>}
      {layout === 1 ? <div className={"grid grid-cols-[minmax(0,_1fr)_minmax(0,_1fr)] min-w-0 min-h-0 gap-3.5 max-md:grid-cols-[minmax(0,_1fr)]"}><div className={"hidden max-md:flex"} role="group" aria-label="窄屏对照资料"><Button variant={mobileSide === 'left' ? 'secondary' : 'ghost'} aria-pressed={mobileSide === 'left'} onClick={() => setMobileSide('left')}>主要文件</Button><Button variant={mobileSide === 'right' ? 'secondary' : 'ghost'} aria-pressed={mobileSide === 'right'} onClick={() => setMobileSide('right')}>对照文件</Button></div>{pane('left')}{pane('right')}</div> : pane('left')}
      {(row || input.clarification) && (layout === 0 ? context : <Collapsible className={"max-h-55 overflow-auto"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">要求、机器摘录与收到的说明</Button></CollapsibleTrigger><CollapsibleContent>{context}</CollapsibleContent></Collapsible>)}
    </div>
    {isPreview() && import.meta.env.DEV && <div className={"absolute bottom-3 left-[50%] flex items-center gap-3 rounded-lg py-1 px-2 bg-muted text-foreground text-xs max-md:bottom-[max(8px,_env(safe-area-inset-bottom))]"} role="toolbar" aria-label="阅读布局"><Button variant="ghost" size="icon-sm" aria-label="上一种阅读布局" onClick={() => selectLayout((layout + 2) % 3)}><ArrowLeft/></Button><span>{labels[layout]}</span><Button variant="ghost" size="icon-sm" aria-label="下一种阅读布局" onClick={() => selectLayout((layout + 1) % 3)}><ArrowRight/></Button></div>}
  </DialogContent>
}

export function SourceReaderButton(props: Props) {
  const [open, setOpen] = useState(false)
  return <Dialog open={open} onOpenChange={setOpen}><DialogTrigger asChild><Button variant={props.variant ?? 'outline'} className={props.className} size="sm" aria-label={props.file ? props.label === props.file.name ? `阅读 ${props.file.name}` : `${props.label ?? '打开原件'} · ${props.file.name}` : undefined}>{props.file ? <FileIcon name={props.file.name}/> : <BookOpen/>}<span className="min-w-0 [overflow-wrap:anywhere]">{props.label ?? '打开原件与证据'}</span></Button></DialogTrigger>{open && <Reader {...props} onClose={() => setOpen(false)}/>}</Dialog>
}

export function SourceReaderDialog({open, onOpenChange, onCloseAutoFocus, ...props}: Props & {open: boolean; onOpenChange: (open: boolean) => void; onCloseAutoFocus?: (event: Event) => void}) {
  return <Dialog open={open} onOpenChange={onOpenChange}>{open && <Reader {...props} onClose={() => onOpenChange(false)} onCloseAutoFocus={onCloseAutoFocus}/>}</Dialog>
}
