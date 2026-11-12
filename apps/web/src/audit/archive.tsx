import {displayTime} from '../helpers'
import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/bfd8b8db93484b159a6ce9d171447c80
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {ChevronDown, ListFilter, RotateCw, Search, Upload, X} from 'lucide-react'
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
import {Segmented} from '../components/segmented'
import {ArchiveHistory} from './history'
import {readReportPackage, readReportPdf, readReportZip, type AuditReportAdapter, type AuditReportPackage, type AuditReportReference} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {useReportPackage} from './report-actions'
import {ReportPackage} from './report-package'
import {historyReportReady, historyReportReference, resultKey, readArchiveAddress, writeArchiveAddress, writeArchivePackageAddress, type HistoryEntry} from './archive-contract'
import type {OriginalRenderer} from './review-content'
import {sameSnapshot} from './result-projection'
import {readAuditRoute} from './routes'
import {conclusionLabel, documentTypeLabel, resultTone} from './status-labels'
import type {ArchiveDocument, ArchiveQuery, AuditArchiveAdapter} from './archive-contract'

const initialQuery: ArchiveQuery = {search: '', category_id: null, document_type: null, customer: null, initiator: null, from_date: null, through_date: null, result: null, page: 1, page_size: 10}

const ranges = [['7', '最近 7 天'], ['30', '最近 30 天'], ['90', '最近 90 天'], ['all', '全部']] as const
type Range = typeof ranges[number][0]
// A preset is a start date counted back from today, today included; the end stays open.
function rangeStart(days: number) {
  const date = new Date(); date.setDate(date.getDate() - days + 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
const rangeOf = (query: ArchiveQuery): Range | null => !query.from_date && !query.through_date ? 'all' : query.through_date ? null : ranges.find(([days]) => days !== 'all' && rangeStart(Number(days)) === query.from_date)?.[0] ?? null
const unset = <span className="text-muted-foreground">—</span>
// The values match what the result column shows: the latest decided conclusion, none yet, or an audit in flight.
const results = [['PASS', conclusionLabel.PASS], ['NG', conclusionLabel.NG], ['UNKNOWN', conclusionLabel.UNKNOWN], ['none', '尚无完成审核'], ['active', '审核中']] as const
const resultName = (value: ArchiveQuery['result']) => results.find(([key]) => key === value)?.[1] ?? null

export function AuditArchive({adapter, onUpload, renderOriginal, onOpenTask, reportAdapter, sharingAdapter, active = true, headerActions}: {adapter: AuditArchiveAdapter; onUpload: () => void; renderOriginal?: OriginalRenderer; onOpenTask: (id: string) => void; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; active?: boolean; headerActions?: HTMLElement | null}) {
  const [query, setQuery] = useState(initialQuery), [search, setSearch] = useState('')
  const [page, setPage] = useState<components['schemas']['AuditArchivePage'] | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [address, setAddress] = useState(() => readArchiveAddress(location.search)), [restore, setRestore] = useState(0)
  const [filtersOpen, setFiltersOpen] = useState(false), [treeSearch, setTreeSearch] = useState('')
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
    }).catch(failure => {if (epoch.current === run) setError(failure instanceof Error && failure.name === 'TimeoutError' ? '读取审核文件超时，请重新读取。' : '审核文件暂时无法读取，请重试。')}).finally(() => {if (epoch.current === run) setLoading(false)})
    return () => {epoch.current += 1}
  }, [adapter, query, reload, browsing])
  function filter(update: Partial<ArchiveQuery>) {setQuery(current => ({...current, ...update, page: 1}))}
  const current = page && sameSnapshot(page.query, query) ? page : null
  const treeNeedle = treeSearch.trim().toLocaleLowerCase(), treeMatch = (label: string) => !treeNeedle || label.toLocaleLowerCase().includes(treeNeedle)
  const categories = (page?.categories || []).map(category => treeMatch(category.label) ? category : {...category, parts: category.parts.filter(part => treeMatch(part.label))}).filter(category => treeMatch(category.label) || category.parts.length)
  const node = (id: string | null, label: string, count: number | string) => <Button variant={query.category_id === id ? 'soft' : 'ghost'} className="min-w-0 flex-1 justify-between" aria-current={query.category_id === id || undefined} onClick={() => filter({category_id: id})}><span className="truncate">{label}</span><span className="text-xs text-muted-foreground tabular-nums">{count}</span></Button>
  const tree = <div className="grid gap-3"><div className="relative"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索车型或零件" placeholder="搜索车型或零件" iconStart value={treeSearch} onChange={event => setTreeSearch(event.target.value)}/></div><nav className="grid gap-0.5" aria-label="车型与零件">{node(null, '全部文件', page?.document_count ?? '—')}{categories.map(category => <Collapsible key={category.id} defaultOpen><div className="flex items-center"><CollapsibleTrigger asChild><Button variant="ghost" size="icon-sm" aria-label={`展开或收起 ${category.label}`} className="group"><ChevronDown className="transition-transform group-data-[state=closed]:-rotate-90"/></Button></CollapsibleTrigger>{node(category.id, category.label === '其他' ? '未归类车型' : category.label, category.count)}</div><CollapsibleContent><div className="ml-3.5 grid gap-0.5 border-l pl-2">{category.parts.map(part => <div key={part.id} className="flex">{node(part.id, part.label === '其他' ? '未归类零件' : part.label, part.count)}</div>)}</div></CollapsibleContent></Collapsible>)}{treeNeedle && !categories.length && <p className="px-2 py-3 text-xs text-muted-foreground">没有匹配的车型或零件。</p>}</nav></div>
  const choices = (label: string, value: string | null, options: string[], key: 'document_type' | 'customer' | 'initiator') => <Select value={value || 'all'} onValueChange={value => filter({[key]: value === 'all' ? null : value})}><SelectTrigger aria-label={label} className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">{label}</SelectItem>{options.map(option => <SelectItem key={option} value={option}>{key === 'document_type' ? documentTypeLabel(option) : option}</SelectItem>)}</SelectContent></Select>
  const range = rangeOf(query), preset = range && range !== 'all' ? ranges.find(([days]) => days === range)?.[1] : null
  const advancedCount = [query.initiator, !preset && query.from_date, query.through_date].filter(Boolean).length
  const categoryLabel = page?.categories.flatMap(category => [{id: category.id, label: category.label}, ...category.parts.map(part => ({id: part.id, label: `${category.label} / ${part.label}`}))]).find(category => category.id === query.category_id)?.label
  const applied = ([['search', '搜索', query.search], ['category_id', '分类', query.category_id ? categoryLabel || '已选分类' : null], ['document_type', '文件类型', query.document_type && documentTypeLabel(query.document_type)], ['customer', '客户', query.customer], ['result', '审核结果', resultName(query.result)], ['initiator', '发起人', query.initiator], ['from_date', preset ? '时间' : '开始日期', preset || query.from_date], ['through_date', '结束日期', query.through_date]] as const).filter(([, , value]) => Boolean(value))
  const clearFilters = () => {setSearch(''); setQuery(initialQuery)}
  const status = (document: ArchiveDocument) => <div className="space-y-1">{document.latest_completed ? <Badge variant={resultTone(document.latest_completed.conclusion)}>{document.latest_completed.conclusion ? conclusionLabel[document.latest_completed.conclusion] ?? '结果未知' : '结果未知'}</Badge> : <span className="text-xs text-muted-foreground">尚无完成审核</span>}{document.latest_completed && <p className="text-xs text-muted-foreground">第 {document.latest_completed.sequence} 次审核</p>}{document.latest_activity && ['queued', 'running'].includes(document.latest_activity.execution) && <p className="text-xs text-muted-foreground">第 {document.latest_activity.sequence} 次审核{document.latest_activity.execution === 'queued' ? '排队中' : '中'}</p>}</div>
  const selection = chosen.length > 0 && <section aria-label="已选固定报告" className="space-y-3"><div className="flex flex-wrap items-center gap-2"><span className="mr-1 text-sm font-medium">已选择 {chosen.length} 份报告</span>{chosen.length > 1 ? <Button variant="outline" size="sm" disabled={!packaging.canCreatePackage(chosenReferences)} onClick={() => void packaging.createPackage(chosenReferences)}>打包所选 {chosen.length} 份历史报告</Button> : <Button variant="outline" size="sm" disabled={!reportAdapter || downloading} onClick={() => void download()}>下载所选 1 份历史报告</Button>}<Button variant="ghost" size="sm" onClick={() => setChecked({})}>清空选择</Button></div><div className="flex flex-wrap gap-2">{chosen.map(item => <Badge variant="outline" className="max-w-full whitespace-normal text-left" key={resultKey(item.entry.reference)}>{item.title} · 第 {item.entry.sequence} 次审核</Badge>)}</div></section>
  if (!active) return null
  if (!address.documentId && (address.reference || address.invalid || address.report)) return <Alert><AlertTitle>无法打开这次审核</AlertTitle><AlertDescription>链接缺少有效的文件或审核信息。<Button variant="outline" onClick={closeDocument}>返回审核文件</Button></AlertDescription></Alert>
  if (address.documentId) return <ArchiveHistory key={`${address.documentId}:${restore}`} documentId={address.documentId} document={page?.documents.find(row => row.document_id === address.documentId) || null} initialAddress={address} onNavigate={(reference, report, replace) => writeArchiveAddress(address.documentId, reference, report, replace)} adapter={adapter} renderOriginal={renderOriginal} onBack={closeDocument} onOpenTask={onOpenTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter}/>
  const upload = <Button onClick={onUpload}><Upload/>上传文件</Button>
  const subtitle = <p className="text-sm text-muted-foreground">所有历史审核文件与结果，可按车型、零件、客户、审核结果与文件类型筛选。</p>
  return <section className="space-y-4" aria-label="文件档案">
    {headerActions ? <><h1 className="sr-only">审核文件</h1>{subtitle}{createPortal(upload, headerActions)}</> : <header className="flex flex-wrap items-end justify-between gap-3 pb-1"><div className="space-y-1"><h1 className="text-2xl font-semibold">审核文件</h1>{subtitle}</div>{upload}</header>}
    <div className="grid grid-cols-[15rem_minmax(0,1fr)] gap-4">
    {/* Below lg the tree opens from the toolbar instead, so the table keeps the width. */}
    <aside aria-label="文件分类" className="self-start rounded-lg border bg-card p-3">{tree}</aside>
    <div className="min-w-0 space-y-4">
    <form role="search" aria-label="筛选审核文件" className="flex flex-wrap items-center gap-2" onSubmit={event => {event.preventDefault(); filter({search})}}>
      <Segmented label="审核时间范围" value={range} options={ranges} onChange={days => filter({from_date: days === 'all' ? null : rangeStart(Number(days)), through_date: null})}/>
      <div className="w-40">{choices('全部客户', query.customer, page?.customers || [], 'customer')}</div>
      <div className="w-40"><Select value={query.result || 'all'} onValueChange={value => filter({result: results.find(([key]) => key === value)?.[0] ?? null})}><SelectTrigger aria-label="全部结果" className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部结果</SelectItem>{results.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
      <div className="w-40">{choices('全部文件类型', query.document_type, page?.document_types || [], 'document_type')}</div>
      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}><SheetTrigger asChild><Button type="button" variant="outline" aria-label="更多筛选"><ListFilter/>筛选{advancedCount > 0 && <Badge variant="secondary">{advancedCount}</Badge>}</Button></SheetTrigger><SheetContent><SheetHeader><SheetTitle>筛选审核文件</SheetTitle><SheetDescription className="sr-only">按发起人和审核时间筛选。</SheetDescription></SheetHeader><div className="grid gap-5"><div className="grid gap-2"><span className="text-sm font-medium">发起人</span>{choices('全部发起人', query.initiator, page?.initiators || [], 'initiator')}</div><label className="grid gap-2 text-sm font-medium">开始日期<Input type="date" value={query.from_date || ''} onChange={event => filter({from_date: event.target.value || null})}/></label><label className="grid gap-2 text-sm font-medium">结束日期<Input type="date" value={query.through_date || ''} onChange={event => filter({through_date: event.target.value || null})}/></label></div><SheetFooter><SheetClose asChild><Button type="button">查看筛选结果</Button></SheetClose><Button type="button" variant="outline" onClick={clearFilters}>清除筛选</Button></SheetFooter></SheetContent></Sheet>
      <div className="relative ml-auto min-w-0 basis-56"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索文件名" placeholder="搜索文件名…" iconStart value={search} onChange={event => setSearch(event.target.value)}/></div><Button type="submit" variant="outline">搜索</Button>
      <Button type="button" variant="outline" size="icon" aria-label="刷新审核文件" disabled={loading} onClick={() => setReload(value => value + 1)}><RotateCw className={loading ? 'animate-spin' : ''}/></Button>
    </form>
    {applied.length > 0 && <div className="flex flex-wrap items-center gap-2" aria-label="已应用筛选">{applied.map(([key, label, value]) => <Button key={key} variant="secondary" size="sm" aria-label={`移除${label}筛选`} className="h-auto min-h-7 whitespace-normal text-left" onClick={() => {if (key === 'search') setSearch(''); filter({[key]: key === 'search' ? '' : null})}}>{label}：{value}<X/></Button>)}<Button variant="ghost" size="sm" onClick={clearFilters}>清除筛选</Button></div>}
    {loading && <ReadSkeleton label="正在读取审核文件…" kind={current ? 'inline' : 'table'}/>}
    {error && <Alert variant="destructive"><AlertTitle>审核文件读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(value => value + 1)}>重新读取审核文件</Button></AlertDescription></Alert>}
    {current && <div className="overflow-hidden rounded-lg border bg-card">
      {/* Twelve columns do not fit a narrow desktop; the table scrolls inside its card, never the page. */}
      <Table aria-label="审核文件列表"><TableHeader variant="muted"><TableRow><TableHead className="w-10"><span className="sr-only">选择报告</span></TableHead><TableHead>文档名称</TableHead><TableHead>审核结果</TableHead><TableHead>文件类型</TableHead><TableHead>审核规则</TableHead><TableHead>发起人</TableHead><TableHead>审核时间</TableHead><TableHead>客户名称</TableHead><TableHead>车型</TableHead><TableHead>零件号</TableHead><TableHead>零件名称</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{current.documents.map(document => {
        const initiator = document.latest_completed?.initiator ?? document.latest_activity?.initiator, rule = document.latest_completed?.rule_title ?? document.latest_activity?.rule_title
        return <TableRow key={document.document_id} data-state={checked[document.document_id] ? 'selected' : undefined} className="cursor-pointer" onClick={event => {if (event.target instanceof Element && event.currentTarget.contains(event.target) && !event.target.closest('button, a, [role="checkbox"]')) openDocument(document)}}>
        <TableCell><Checkbox aria-label={`选择 ${document.title} 档案 ${document.document_id.split('-').at(-1)}`} checked={Boolean(checked[document.document_id])} disabled={!checked[document.document_id] && (!document.latest_completed || !historyReportReady(document.latest_completed))} onCheckedChange={value => toggle(document, value === true)}/></TableCell>
        <TableCell className="whitespace-normal"><div className="min-w-64 max-w-96 py-1"><FileName name={document.title} colored/></div></TableCell>
        <TableCell>{status(document)}</TableCell>
        <TableCell>{document.document_type ? documentTypeLabel(document.document_type) : unset}</TableCell>
        <TableCell className="whitespace-normal"><div className="min-w-32 max-w-56">{rule || unset}</div></TableCell>
        <TableCell>{initiator || unset}</TableCell>
        <TableCell>{document.latest_completed ? <time className="text-muted-foreground tabular-nums" dateTime={document.latest_completed.audited_at} title={displayTime(document.latest_completed.audited_at)}>{displayTime(document.latest_completed.audited_at)}</time> : unset}</TableCell>
        <TableCell>{document.metadata.customer || unset}</TableCell>
        <TableCell>{document.metadata.model || unset}</TableCell>
        <TableCell>{document.metadata.part_number ? <span className="tabular-nums">{document.metadata.part_number}</span> : unset}</TableCell>
        <TableCell>{document.metadata.part || unset}</TableCell>
        <TableCell className="text-right"><Button variant="link" size="sm" aria-label="查看审核记录" title="查看审核记录" onClick={() => openDocument(document)}>查看</Button></TableCell>
      </TableRow>})}</TableBody></Table>
      {!current.documents.length && <div className="grid justify-items-center gap-3 py-12 text-center"><p className="text-sm text-muted-foreground">当前筛选下没有文件。</p>{applied.length > 0 && <Button variant="outline" size="sm" onClick={clearFilters}>清空条件</Button>}</div>}
      <footer className="space-y-3 border-t px-3 py-3">
        {selection}
        <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">共 {current.total} 份文件</p><div className="flex flex-wrap items-center gap-2"><span className="mr-2 text-xs text-muted-foreground">第 {query.page} 页 · 每页 10 份</span><Button variant="outline" size="sm" disabled={query.page === 1} onClick={() => setQuery({...query, page: query.page - 1})}>上一页</Button><Button variant="outline" size="sm" disabled={query.page * 10 >= current.total} onClick={() => setQuery({...query, page: query.page + 1})}>下一页</Button></div></div>
      </footer>
    </div>}
    {!current && selection}
    </div>
    </div>
    {packageReading && <ReadSkeleton label="正在恢复 ZIP 工作…" kind={job ? 'inline' : 'detail'}/>}
    {packageError && <Alert variant="destructive"><AlertTitle>ZIP 暂不可用</AlertTitle><AlertDescription><p>{packageError}</p><Button variant="outline" disabled={packageReading} onClick={() => void restorePackage()}>重新读取 ZIP</Button></AlertDescription></Alert>}
    <ReportPackage job={job} nameFor={reference => known.current.get(resultKey(reference))?.title || '历史文件'} busy={packaging.packageBusy || downloading || packageReading} onRead={() => void packaging.readPackage()} onDownload={() => void download(true)} onRetry={() => {if (job?.state === 'failed' && job.retry_allowed) void packaging.createPackage(job.reports)}} downloadError={downloadError}/>
    {!job && downloadError && <p role="alert">{downloadError}</p>}
  </section>
}
