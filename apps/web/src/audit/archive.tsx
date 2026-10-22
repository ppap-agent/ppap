import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/bfd8b8db93484b159a6ce9d171447c80
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {ArrowRight, ChevronDown, FolderTree, ListFilter, RotateCw, Search, Upload, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {ArchiveHistory} from './history'
import {readReportPackage, readReportPdf, readReportZip, type AuditReportAdapter, type AuditReportPackage, type AuditReportReference} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {useReportPackage} from './report-actions'
import {ReportPackage} from './report-package'
import {historyReportReady, historyReportReference, resultKey, readArchiveAddress, writeArchiveAddress, writeArchivePackageAddress, type HistoryEntry} from './archive-contract'
import type {OriginalRenderer} from './review-content'
import {sameSnapshot} from './result-projection'
import {readAuditRoute} from './routes'
import type {ArchiveDocument, ArchiveQuery, AuditArchiveAdapter} from './archive-contract'

const initialQuery: ArchiveQuery = {search: '', category_id: null, document_type: null, customer: null, initiator: null, from_date: null, through_date: null, page: 1, page_size: 10}

export function AuditArchive({adapter, onUpload, renderOriginal, onOpenTask, reportAdapter, sharingAdapter, active = true, headerActions}: {adapter: AuditArchiveAdapter; onUpload: () => void; renderOriginal?: OriginalRenderer; onOpenTask: (id: string) => void; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; active?: boolean; headerActions?: HTMLElement | null}) {
  const [query, setQuery] = useState(initialQuery), [search, setSearch] = useState('')
  const [page, setPage] = useState<components['schemas']['AuditArchivePage'] | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [treeOpen, setTreeOpen] = useState(false), [address, setAddress] = useState(() => readArchiveAddress(location.search)), [restore, setRestore] = useState(0)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [packageRequest, setPackageRequest] = useState(() => new URLSearchParams(location.search).get('packageRequest'))
  const packageRequestRef = useRef(packageRequest), packageEpoch = useRef(0), activeRef = useRef(active)
  activeRef.current = active
  const epoch = useRef(0)
  useEffect(() => {
    if (!active) return
    const changed = () => {
      const params = new URLSearchParams(location.search)
      if (readAuditRoute(new URL(location.href)).surface !== 'archive') return
      const requestId = params.get('packageRequest')
      if (requestId !== packageRequestRef.current) {packageEpoch.current += 1; packageRequestRef.current = requestId; setPackageRequest(requestId); setJob(null); setPackageError(''); setPackageReading(false)}
      setAddress(readArchiveAddress(location.search)); setRestore(value => value + 1)
    }
    if (!new URLSearchParams(location.search).has('packageRequest') && packageRequestRef.current) writeArchivePackageAddress(packageRequestRef.current)
    if (!sameSnapshot(address, readArchiveAddress(location.search)) || new URLSearchParams(location.search).get('packageRequest') !== packageRequestRef.current) changed()
    window.addEventListener('popstate', changed)
    return () => window.removeEventListener('popstate', changed)
  }, [active])
  function openDocument(document: ArchiveDocument) {setAddress({documentId: document.document_id, reference: null, invalid: false, report: false}); writeArchiveAddress(document.document_id, null)}
  function closeDocument() {setAddress({documentId: null, reference: null, invalid: false, report: false}); writeArchiveAddress(null, null)}
  const [checked, setChecked] = useState<Record<string, {entry: HistoryEntry; title: string}>>({})
  const known = useRef(new Map<string, {entry: HistoryEntry; title: string}>())
  const [job, setJob] = useState<AuditReportPackage | null>(null), [downloadError, setDownloadError] = useState(''), [downloading, setDownloading] = useState(false)
  const [packageReading, setPackageReading] = useState(false), [packageError, setPackageError] = useState('')
  const alive = useRef(true)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  function commitPackage(next: AuditReportPackage) {
    const requestId = next.receipt.state === 'rejected' && !next.package_id ? null : next.receipt.request_id
    packageEpoch.current += 1; packageRequestRef.current = requestId
    setPackageRequest(requestId); setJob(next); setPackageError(''); setPackageReading(false)
    if (activeRef.current && readAuditRoute(new URL(location.href)).surface === 'archive') writeArchivePackageAddress(requestId)
  }
  const packaging = useReportPackage(job, commitPackage, reference => {
    const item = known.current.get(resultKey(reference))
    return Boolean(item && historyReportReady(item.entry) && sameSnapshot(historyReportReference(item.entry), reference) || job?.reports.some(row => sameSnapshot(row, reference)))
  }, reportAdapter)
  async function restorePackage() {
    const requestId = packageRequestRef.current
    if (!requestId || !reportAdapter) return
    const request = ++packageEpoch.current
    setPackageReading(true); setPackageError('')
    try {
      const next = await readReportPackage(reportAdapter, requestId)
      if (activeRef.current && request === packageEpoch.current && packageRequestRef.current === requestId) setJob(next)
    } catch {if (activeRef.current && request === packageEpoch.current) setPackageError('未能读取此打包请求。请重新读取，保留原请求，不会重新打包。')}
    finally {if (activeRef.current && request === packageEpoch.current) setPackageReading(false)}
  }
  useEffect(() => {
    if (!active || !packageRequest || job?.receipt.request_id === packageRequest) return
    void restorePackage()
    return () => {packageEpoch.current += 1}
  }, [active, packageRequest, reportAdapter])
  const chosen = Object.values(checked)
  const chosenReferences = chosen.map(item => historyReportReference(item.entry)!)
  function toggle(document: ArchiveDocument, value: boolean) {
    if (value && document.latest_completed && historyReportReady(document.latest_completed)) {
      const item = {entry: structuredClone(document.latest_completed), title: document.title}
      known.current.set(resultKey(item.entry.reference), item)
      setChecked(rows => ({...rows, [document.document_id]: item}))
    } else setChecked(rows => Object.fromEntries(Object.entries(rows).filter(([id]) => id !== document.document_id)))
  }
  async function download(asPackage = false) {
    if (!reportAdapter || downloading) return
    setDownloading(true); setDownloadError('')
    let url = ''
    try {
      const item = chosen[0], reference = item ? historyReportReference(item.entry) : null
      const artifact = job?.artifact
      const blob = asPackage && job ? await readReportZip(reportAdapter, job) : reference && item.entry.report.artifact ? await readReportPdf(reportAdapter, reference, item.entry.report.artifact) : null
      if (!blob) throw new Error('invalid_archive_download')
      if (!alive.current) return
      url = URL.createObjectURL(blob); const link = window.document.createElement('a'); link.href = url; link.download = asPackage && job ? artifact!.filename : item.entry.report.artifact!.filename; link.click()
    } catch {if (alive.current) setDownloadError('下载未完成，请重试。')}
    finally {if (url) URL.revokeObjectURL(url); if (alive.current) setDownloading(false)}
  }
  const browsing = active && !address.documentId && !address.invalid
  useEffect(() => {
    if (!browsing) return
    const run = ++epoch.current
    setLoading(true); setError('')
    void adapter.listDocuments(structuredClone(query)).then(result => {
      if (epoch.current !== run) return
      if (!sameSnapshot(result.query, query) || new Set(result.documents.map(row => row.document_id)).size !== result.documents.length || result.documents.some(row => row.latest_completed && (row.latest_completed.document_id !== row.document_id || row.latest_completed.execution !== 'completed') || row.latest_activity && row.latest_activity.document_id !== row.document_id)) throw new Error('archive_query_mismatch')
      setPage(result)
    }).catch(failure => {if (epoch.current === run) setError(failure instanceof Error && failure.name === 'TimeoutError' ? '读取审核档案超时，请重新读取。' : '文件档案暂时无法读取，请重试。')}).finally(() => {if (epoch.current === run) setLoading(false)})
    return () => {epoch.current += 1}
  }, [adapter, query, reload, browsing])
  function filter(update: Partial<ArchiveQuery>) {setQuery(current => ({...current, ...update, page: 1}))}
  const current = page && sameSnapshot(page.query, query) ? page : null
  const tree = <nav className="grid gap-1" aria-label="车型与零件"><Button variant={!query.category_id ? 'secondary' : 'ghost'} className="justify-between" onClick={() => {filter({category_id: null}); setTreeOpen(false)}}>全部文件 <span className="text-muted-foreground">{page?.document_count ?? '—'}</span></Button>{page?.categories.map(category => <Collapsible key={category.id} defaultOpen><div className="flex items-center"><CollapsibleTrigger asChild><Button variant="ghost" size="icon" aria-label={`展开或收起 ${category.label}`} className="group"><ChevronDown className="transition-transform group-data-[state=closed]:-rotate-90"/></Button></CollapsibleTrigger><Button variant={query.category_id === category.id ? 'secondary' : 'ghost'} className="min-w-0 flex-1 justify-between" onClick={() => {filter({category_id: category.id}); setTreeOpen(false)}}>{category.label === '其他' ? '未归类车型' : category.label}<span className="text-muted-foreground">{category.count}</span></Button></div><CollapsibleContent><div className="ml-5 grid gap-1 border-l pl-3">{category.parts.map(part => <Button key={part.id} className="justify-between" variant={query.category_id === part.id ? 'secondary' : 'ghost'} onClick={() => {filter({category_id: part.id}); setTreeOpen(false)}}>{part.label === '其他' ? '未归类零件' : part.label}<span className="text-muted-foreground">{part.count}</span></Button>)}</div></CollapsibleContent></Collapsible>)}</nav>
  const choices = (label: string, value: string | null, options: string[], key: 'document_type' | 'customer' | 'initiator') => <Select value={value || 'all'} onValueChange={value => filter({[key]: value === 'all' ? null : value})}><SelectTrigger aria-label={label} className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">{label}</SelectItem>{options.map(option => <SelectItem key={option} value={option}>{option}</SelectItem>)}</SelectContent></Select>
  const advancedCount = [query.customer, query.initiator, query.from_date, query.through_date].filter(Boolean).length
  const categoryLabel = page?.categories.flatMap(category => [{id: category.id, label: category.label}, ...category.parts.map(part => ({id: part.id, label: `${category.label} / ${part.label}`}))]).find(category => category.id === query.category_id)?.label
  const applied = ([['search', '搜索', query.search], ['category_id', '分类', query.category_id ? categoryLabel || '已选分类' : null], ['document_type', '文件类型', query.document_type], ['customer', '客户', query.customer], ['initiator', '发起人', query.initiator], ['from_date', '开始日期', query.from_date], ['through_date', '结束日期', query.through_date]] as const).filter(([, , value]) => Boolean(value))
  const clearFilters = () => {setSearch(''); setQuery(initialQuery)}
  const status = (document: ArchiveDocument) => <div className="space-y-1.5">{document.latest_completed ? <Badge shape="square" variant={document.latest_completed.conclusion === 'NG' ? 'destructive' : document.latest_completed.conclusion === 'PASS' ? 'success' : 'outline'}>第 {document.latest_completed.sequence} 次审核 · {document.latest_completed.conclusion || '结果未知'}</Badge> : <span className="text-xs text-muted-foreground">尚无完成审核</span>}{document.latest_activity && ['queued', 'running'].includes(document.latest_activity.execution) && <p className="text-xs text-muted-foreground">第 {document.latest_activity.sequence} 次审核{document.latest_activity.execution === 'queued' ? '排队中' : '中'}</p>}</div>
  const selection = chosen.length > 0 && <section aria-label="已选固定报告" className="space-y-3"><div className="flex flex-wrap items-center gap-2"><span className="mr-1 text-sm font-medium">已选择 {chosen.length} 份报告</span>{chosen.length > 1 ? <Button variant="outline" size="sm" disabled={!packaging.canCreatePackage(chosenReferences)} onClick={() => void packaging.createPackage(chosenReferences)}>打包所选 {chosen.length} 份历史报告</Button> : <Button variant="outline" size="sm" disabled={!reportAdapter || downloading} onClick={() => void download()}>下载所选 1 份历史报告</Button>}<Button variant="ghost" size="sm" onClick={() => setChecked({})}>清空选择</Button></div><div className="flex flex-wrap gap-2">{chosen.map(item => <Badge variant="outline" className="max-w-full whitespace-normal text-left" key={resultKey(item.entry.reference)}>{item.title} · 第 {item.entry.sequence} 次审核</Badge>)}</div></section>
  if (!active) return null
  if (!address.documentId && (address.reference || address.invalid || address.report)) return <Alert><AlertTitle>无法打开这次审核</AlertTitle><AlertDescription>链接缺少有效的文件或审核信息。<Button variant="outline" onClick={closeDocument}>返回文件档案</Button></AlertDescription></Alert>
  if (address.documentId) return <ArchiveHistory key={`${address.documentId}:${restore}`} documentId={address.documentId} document={page?.documents.find(row => row.document_id === address.documentId) || null} initialAddress={address} onNavigate={(reference, report, replace) => writeArchiveAddress(address.documentId, reference, report, replace)} adapter={adapter} renderOriginal={renderOriginal} onBack={closeDocument} onOpenTask={onOpenTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter}/>
  const upload = <Button onClick={onUpload}><Upload/>上传文件</Button>
  return <section className="space-y-4" aria-label="文件档案">
    {headerActions ? <><h1 className="sr-only">审核文件</h1>{createPortal(upload, headerActions)}</> : <header className="flex flex-wrap items-center justify-between gap-3 pb-1"><h1 className="text-2xl font-semibold">审核文件</h1>{upload}</header>}
    <form role="search" aria-label="筛选审核文件" className="flex flex-wrap items-center gap-2 rounded-lg border p-3" onSubmit={event => {event.preventDefault(); filter({search})}}>
      <div className="relative min-w-0 flex-1 basis-60"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索文档名" placeholder="搜索文件名…" iconStart value={search} onChange={event => setSearch(event.target.value)}/></div><Button type="submit" variant="outline">搜索</Button>
      <div className="w-44 max-sm:w-full">{choices('全部文件类型', query.document_type, page?.document_types || [], 'document_type')}</div>
      <Sheet open={treeOpen} onOpenChange={setTreeOpen}><SheetTrigger asChild><Button type="button" variant="outline" aria-label="车型与零件"><FolderTree/>分类{query.category_id && <span className="size-1.5 rounded-full bg-foreground"/>}<ChevronDown/></Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>车型与零件</SheetTitle><SheetDescription className="sr-only">按车型和零件查找文件。</SheetDescription></SheetHeader><div className="min-h-0 flex-1 overflow-y-auto">{tree}</div></SheetContent></Sheet>
      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}><SheetTrigger asChild><Button type="button" variant="outline" aria-label="更多筛选"><ListFilter/>筛选{advancedCount > 0 && <Badge variant="secondary">{advancedCount}</Badge>}</Button></SheetTrigger><SheetContent><SheetHeader><SheetTitle>筛选审核文件</SheetTitle><SheetDescription className="sr-only">按客户、发起人和审核时间筛选。</SheetDescription></SheetHeader><div className="grid gap-5"><div className="grid gap-2"><span className="text-sm font-medium">客户</span>{choices('全部客户', query.customer, page?.customers || [], 'customer')}</div><div className="grid gap-2"><span className="text-sm font-medium">发起人</span>{choices('全部发起人', query.initiator, page?.initiators || [], 'initiator')}</div><label className="grid gap-2 text-sm font-medium">开始日期<Input type="date" value={query.from_date || ''} onChange={event => filter({from_date: event.target.value || null})}/></label><label className="grid gap-2 text-sm font-medium">结束日期<Input type="date" value={query.through_date || ''} onChange={event => filter({through_date: event.target.value || null})}/></label></div><SheetFooter><SheetClose asChild><Button type="button">查看筛选结果</Button></SheetClose><Button type="button" variant="outline" onClick={clearFilters}>清除筛选</Button></SheetFooter></SheetContent></Sheet>
      <Button type="button" variant="outline" size="icon" aria-label="刷新档案" className="ml-auto" disabled={loading} onClick={() => setReload(value => value + 1)}><RotateCw className={loading ? 'animate-spin' : ''}/></Button>
    </form>
    {applied.length > 0 && <div className="flex flex-wrap items-center gap-2" aria-label="已应用筛选">{applied.map(([key, label, value]) => <Button key={key} variant="secondary" size="sm" aria-label={`移除${label}筛选`} className="h-auto min-h-7 whitespace-normal text-left" onClick={() => {if (key === 'search') setSearch(''); filter({[key]: key === 'search' ? '' : null})}}>{label}：{value}<X/></Button>)}<Button variant="ghost" size="sm" onClick={clearFilters}>清除筛选</Button></div>}
    {loading && <ReadSkeleton label="正在读取文件档案…" kind={current ? 'inline' : 'table'}/>}
    {error && <Alert variant="destructive"><AlertTitle>档案读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(value => value + 1)}>重新读取档案</Button></AlertDescription></Alert>}
    {current && <div className="overflow-hidden rounded-lg border">
      <Table aria-label="审核文件档案" className="table-fixed"><TableHeader variant="muted"><TableRow><TableHead className="w-10"><span className="sr-only">选择报告</span></TableHead><TableHead>文件</TableHead><TableHead className="w-44 max-md:hidden">最新完成审核</TableHead><TableHead className="w-32 max-lg:hidden">审核时间</TableHead><TableHead className="w-40 text-right max-md:hidden">操作</TableHead></TableRow></TableHeader><TableBody>{current.documents.map(document => <TableRow key={document.document_id} data-state={checked[document.document_id] ? 'selected' : undefined}>
        <TableCell><Checkbox aria-label={`选择 ${document.title} 档案 ${document.document_id.split('-').at(-1)}`} checked={Boolean(checked[document.document_id])} disabled={!checked[document.document_id] && (!document.latest_completed || !historyReportReady(document.latest_completed))} onCheckedChange={value => toggle(document, value === true)}/></TableCell>
        <TableCell className="whitespace-normal"><div className="space-y-1.5 py-1.5"><FileName name={document.title} colored/><p className="pl-8 text-xs leading-relaxed text-muted-foreground">{[document.document_type, document.metadata.customer, document.metadata.model, document.metadata.part].filter(Boolean).join(' · ') || '未提供客户及零件信息'}</p></div><div className="mt-3 space-y-2 md:hidden">{status(document)}<Button variant="outline" size="sm" onClick={() => openDocument(document)}>查看审核记录<ArrowRight/></Button></div></TableCell>
        <TableCell className="whitespace-normal max-md:hidden">{status(document)}</TableCell>
        <TableCell className="max-lg:hidden">{document.latest_completed ? <time className="text-xs leading-relaxed text-muted-foreground tabular-nums" dateTime={document.latest_completed.audited_at} title={new Date(document.latest_completed.audited_at).toLocaleString('zh-CN', {hour12: false})}>{new Date(document.latest_completed.audited_at).toLocaleDateString('zh-CN')}<br/>{new Date(document.latest_completed.audited_at).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', hour12: false})}</time> : <span className="text-muted-foreground">—</span>}</TableCell>
        <TableCell className="text-right max-md:hidden"><Button variant="ghost" size="sm" className="whitespace-nowrap" onClick={() => openDocument(document)}>查看审核记录<ArrowRight/></Button></TableCell>
      </TableRow>)}</TableBody></Table>
      {!current.documents.length && <p className="py-12 text-center text-sm text-muted-foreground">当前筛选下没有文档。</p>}
      <footer className="space-y-3 border-t bg-muted/20 px-3 py-3">
        {selection}
        <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">共 {current.total} 份文档</p><div className="flex flex-wrap items-center gap-2"><span className="mr-2 text-xs text-muted-foreground">第 {query.page} 页 · 每页 10 份</span><Button variant="outline" size="sm" disabled={query.page === 1} onClick={() => setQuery({...query, page: query.page - 1})}>上一页</Button><Button variant="outline" size="sm" disabled={query.page * 10 >= current.total} onClick={() => setQuery({...query, page: query.page + 1})}>下一页</Button></div></div>
      </footer>
    </div>}
    {!current && selection}
    {packageReading && <ReadSkeleton label="正在恢复 ZIP 工作…" kind={job ? 'inline' : 'detail'}/>}
    {packageError && <Alert variant="destructive"><AlertTitle>ZIP 暂不可用</AlertTitle><AlertDescription><p>{packageError}</p><Button variant="outline" disabled={packageReading} onClick={() => void restorePackage()}>重新读取 ZIP</Button></AlertDescription></Alert>}
    <ReportPackage job={job} nameFor={reference => known.current.get(resultKey(reference))?.title || '历史文件'} busy={packaging.packageBusy || downloading || packageReading} onRead={() => void packaging.readPackage()} onDownload={() => void download(true)} onRetry={() => {if (job?.state === 'failed' && job.retry_allowed) void packaging.createPackage(job.reports)}} downloadError={downloadError}/>
    {!job && downloadError && <p role="alert">{downloadError}</p>}
  </section>
}
