import {displayTime} from '../helpers'
import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/bfd8b8db93484b159a6ce9d171447c80
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {ListFilter, RotateCw, Search, Upload, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {Segmented} from '../components/segmented'
import {Pager} from '../components/pager'
import {cn} from '../lib/utils'
import {ArchiveHistory} from './history'
import {readReportPackage, readReportPdf, readReportZip, type AuditReportAdapter, type AuditReportPackage} from './report-contract'
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
const stamp = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
const otherLabel = (label: string, kind: string) => label === '其他' ? `未归类${kind}` : label

export function AuditArchive({adapter, onUpload, renderOriginal, onOpenTask, reportAdapter, sharingAdapter, active = true, headerActions}: {adapter: AuditArchiveAdapter; onUpload: () => void; renderOriginal?: OriginalRenderer; onOpenTask: (id: string) => void; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; active?: boolean; headerActions?: HTMLElement | null}) {
  const [query, setQuery] = useState(initialQuery), [search, setSearch] = useState('')
  const [page, setPage] = useState<components['schemas']['AuditArchivePage'] | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [address, setAddress] = useState(() => readArchiveAddress(location.search)), [restore, setRestore] = useState(0)
  const [filtersOpen, setFiltersOpen] = useState(false), [readAt, setReadAt] = useState<Date | null>(null)
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
      setPage(result); setReadAt(new Date())
    }).catch(failure => {if (epoch.current === run) setError(failure instanceof Error && failure.name === 'TimeoutError' ? '读取审核文件超时，请重新读取。' : '审核文件暂时无法读取，请重试。')}).finally(() => {if (epoch.current === run) setLoading(false)})
    return () => {epoch.current += 1}
  }, [adapter, query, reload, browsing])
  function filter(update: Partial<ArchiveQuery>) {setQuery(current => ({...current, ...update, page: 1}))}
  // Typing searches after a short pause; both search boxes edit the same words.
  useEffect(() => {
    const words = search.trim()
    if (words === query.search) return
    const timer = window.setTimeout(() => filter({search: words}), 300)
    return () => window.clearTimeout(timer)
  }, [search, query.search])
  const current = page && sameSnapshot(page.query, query) ? page : null
  // Customers lead the tree; the vehicle and part levels open under the chosen customer and vehicle.
  const node = (label: string, count: number | string, current: boolean, update: Partial<ArchiveQuery>, depth = 0) => <div className={cn(depth === 1 && 'pl-3', depth === 2 && 'pl-6')}><Button variant={current ? 'soft' : 'ghost'} size="sm" className="w-full justify-between" aria-current={current || undefined} onClick={() => filter(update)}><span className="truncate">{label}</span><span className="text-xs tabular-nums opacity-70">{count}</span></Button></div>
  const tree = <nav className="grid gap-0.5" aria-label="客户与车型">
    {node('全部文件', page?.document_count ?? '—', !query.customer && !query.category_id, {customer: null, category_id: null})}
    {(page?.customer_groups || []).map(group => <div key={group.label} className="grid gap-0.5">
      {node(group.label, group.count, query.customer === group.label && !query.category_id, {customer: group.label, category_id: null})}
      {query.customer === group.label && group.categories.map(category => {
        const open = query.category_id === category.id || category.parts.some(part => part.id === query.category_id)
        return <div key={category.id} className="grid gap-0.5">
          {node(otherLabel(category.label, '车型'), category.count, query.category_id === category.id, {customer: group.label, category_id: category.id}, 1)}
          {open && category.parts.map(part => <div key={part.id}>{node(otherLabel(part.label, '零件'), part.count, query.category_id === part.id, {customer: group.label, category_id: part.id}, 2)}</div>)}
        </div>
      })}
    </div>)}
  </nav>
  const choices = (label: string, value: string | null, options: string[], key: 'document_type' | 'customer' | 'initiator') => <Select value={value || 'all'} onValueChange={value => filter({[key]: value === 'all' ? null : value})}><SelectTrigger aria-label={label} className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">{label}</SelectItem>{options.map(option => <SelectItem key={option} value={option}>{key === 'document_type' ? documentTypeLabel(option) : option}</SelectItem>)}</SelectContent></Select>
  const range = rangeOf(query), preset = range && range !== 'all' ? ranges.find(([days]) => days === range)?.[1] : null
  const advancedCount = [query.initiator, query.from_date, query.through_date].filter(Boolean).length
  const categoryLabel = page?.categories.flatMap(category => [{id: category.id, label: otherLabel(category.label, '车型')}, ...category.parts.map(part => ({id: part.id, label: `${otherLabel(category.label, '车型')} / ${otherLabel(part.label, '零件')}`}))]).find(category => category.id === query.category_id)?.label
  const applied = ([['search', '搜索', query.search], ['category_id', '分类', query.category_id ? categoryLabel || '已选分类' : null], ['document_type', '文件类型', query.document_type && documentTypeLabel(query.document_type)], ['customer', '客户', query.customer], ['result', '审核结果', resultName(query.result)], ['initiator', '发起人', query.initiator], ['from_date', preset ? '时间' : '开始日期', preset || query.from_date], ['through_date', '结束日期', query.through_date]] as const).filter(([, , value]) => Boolean(value))
  const clearFilters = () => {setSearch(''); setQuery(initialQuery)}
  const status = (document: ArchiveDocument) => <div className="space-y-1">{document.latest_completed ? <Badge variant={resultTone(document.latest_completed.conclusion)}>{document.latest_completed.conclusion ? conclusionLabel[document.latest_completed.conclusion] ?? '结果未知' : '结果未知'}</Badge> : <span className="text-xs text-muted-foreground">尚无完成审核</span>}{document.latest_completed && <p className="text-xs text-muted-foreground">第 {document.latest_completed.sequence} 次审核</p>}{document.latest_activity && ['queued', 'running'].includes(document.latest_activity.execution) && <p className="text-xs text-muted-foreground">第 {document.latest_activity.sequence} 次审核{document.latest_activity.execution === 'queued' ? '排队中' : '中'}</p>}</div>
  const selection = chosen.length > 0 && <section aria-label="已选固定报告" className="space-y-3"><div className="flex flex-wrap items-center gap-2"><span className="mr-1 text-sm font-medium">已选择 {chosen.length} 份报告</span>{chosen.length > 1 ? <Button variant="outline" size="sm" disabled={!packaging.canCreatePackage(chosenReferences)} onClick={() => void packaging.createPackage(chosenReferences)}>打包所选 {chosen.length} 份历史报告</Button> : <Button variant="outline" size="sm" disabled={!reportAdapter || downloading} onClick={() => void download()}>下载所选 1 份历史报告</Button>}<Button variant="ghost" size="sm" onClick={() => setChecked({})}>清空选择</Button></div><div className="flex flex-wrap gap-2">{chosen.map(item => <Badge variant="outline" className="max-w-full whitespace-normal text-left" key={resultKey(item.entry.reference)}>{item.title} · 第 {item.entry.sequence} 次审核</Badge>)}</div></section>
  if (!active) return null
  if (!address.documentId && (address.reference || address.invalid || address.report)) return <Alert><AlertTitle>无法打开这次审核</AlertTitle><AlertDescription>链接缺少有效的文件或审核信息。<Button variant="outline" onClick={closeDocument}>返回审核文件</Button></AlertDescription></Alert>
  // A history read that settles after the person left this document must not write it back into the address.
  if (address.documentId) return <ArchiveHistory key={`${address.documentId}:${restore}`} documentId={address.documentId} document={page?.documents.find(row => row.document_id === address.documentId) || null} initialAddress={address} onNavigate={(reference, report, replace) => {if (activeRef.current && readArchiveAddress(location.search).documentId === address.documentId) writeArchiveAddress(address.documentId, reference, report, replace)}} adapter={adapter} renderOriginal={renderOriginal} onBack={closeDocument} onOpenTask={onOpenTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter}/>
  const upload = <Button onClick={onUpload}><Upload/>上传文件</Button>
  const searchBox = (label: string, placeholder: string, className?: string) => <div className={cn('relative min-w-0', className)}><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input type="search" aria-label={label} placeholder={placeholder} iconStart value={search} onChange={event => setSearch(event.target.value)} onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault(); filter({search: search.trim()})}}}/></div>
  const time = (document: ArchiveDocument) => (document.latest_completed ?? document.latest_activity)?.audited_at
  return <section className="space-y-4" aria-label="文件档案">
    {headerActions && createPortal(upload, headerActions)}
    <div className="grid grid-cols-[15rem_minmax(0,1fr)] gap-4">
    <aside aria-label="文件分类" className="sticky top-0 grid max-h-[calc(100dvh-7rem)] gap-3 self-start overflow-y-auto rounded-lg border bg-card p-3">
      <p aria-hidden="true" className="px-1 text-base font-semibold">审核文件</p>
      {searchBox('搜索文件或零件号', '搜索文件 / 零件号')}
      {tree}
    </aside>
    <div className="min-w-0 space-y-4">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1"><h1 className="text-xl font-semibold">审核文件</h1><p className="text-xs text-muted-foreground">{current && <><span>共 {current.total} 份文件</span> · </>}按客户与车型归集，可追溯历史版本</p></div>
      {!headerActions && upload}
    </header>
    <div role="search" aria-label="筛选审核文件" className="flex flex-wrap items-center gap-1.5">
      {searchBox('搜索文档', '搜索文档名称', 'w-40')}
      <div className="w-32"><Select value={query.customer || 'all'} onValueChange={value => filter({customer: value === 'all' ? null : value, category_id: null})}><SelectTrigger aria-label="全部客户" className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部客户</SelectItem>{(page?.customers || []).map(option => <SelectItem key={option} value={option}>{option}</SelectItem>)}</SelectContent></Select></div>
      <div className="w-32"><Select value={query.result || 'all'} onValueChange={value => filter({result: results.find(([key]) => key === value)?.[0] ?? null})}><SelectTrigger aria-label="全部结果" className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部结果</SelectItem>{results.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
      <div className="w-32">{choices('全部文件类型', query.document_type, page?.document_types || [], 'document_type')}</div>
      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}><SheetTrigger asChild><Button type="button" variant="outline" aria-label="更多筛选"><ListFilter/>筛选{advancedCount > 0 && <Badge variant="secondary">{advancedCount}</Badge>}</Button></SheetTrigger><SheetContent><SheetHeader><SheetTitle>筛选审核文件</SheetTitle><SheetDescription className="sr-only">按发起人和发起时间筛选。</SheetDescription></SheetHeader><div className="grid gap-5"><div className="grid gap-2"><span className="text-sm font-medium">发起人</span>{choices('全部发起人', query.initiator, page?.initiators || [], 'initiator')}</div><div className="grid gap-2"><span className="text-sm font-medium">发起时间</span><Segmented label="审核时间范围" value={range} options={ranges} onChange={days => filter({from_date: days === 'all' ? null : rangeStart(Number(days)), through_date: null})}/></div><label className="grid gap-2 text-sm font-medium">开始日期<Input type="date" value={query.from_date || ''} onChange={event => filter({from_date: event.target.value || null})}/></label><label className="grid gap-2 text-sm font-medium">结束日期<Input type="date" value={query.through_date || ''} onChange={event => filter({through_date: event.target.value || null})}/></label></div><SheetFooter><SheetClose asChild><Button type="button">查看筛选结果</Button></SheetClose><Button type="button" variant="outline" onClick={clearFilters}>清除筛选</Button></SheetFooter></SheetContent></Sheet>
      <div className="ml-auto flex items-center gap-2"><span className="text-xs whitespace-nowrap text-muted-foreground">{readAt && <>数据更新于 <time dateTime={readAt.toISOString()}>{stamp(readAt)}</time></>}</span><Button type="button" variant="outline" size="icon-sm" aria-label="刷新审核文件" disabled={loading} onClick={() => setReload(value => value + 1)}><RotateCw className={loading ? 'animate-spin' : ''}/></Button></div>
    </div>
    {applied.length > 0 && <div className="flex flex-wrap items-center gap-2" aria-label="已应用筛选">{applied.map(([key, label, value]) => <Button key={key} variant="secondary" size="sm" aria-label={`移除${label}筛选`} className="h-auto min-h-7 whitespace-normal text-left" onClick={() => {if (key === 'search') setSearch(''); filter({[key]: key === 'search' ? '' : null})}}>{label}：{value}<X/></Button>)}<Button variant="ghost" size="sm" onClick={clearFilters}>清除筛选</Button></div>}
    {loading && <ReadSkeleton label="正在读取审核文件…" kind={current ? 'inline' : 'table'}/>}
    {error && <Alert variant="destructive"><AlertTitle>审核文件读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(value => value + 1)}>重新读取审核文件</Button></AlertDescription></Alert>}
    {current && <div className="overflow-hidden rounded-lg border bg-card">
      {/* Twelve columns do not fit a narrow desktop; the table scrolls inside its card, never the page. */}
      <Table aria-label="审核文件列表"><TableHeader variant="muted"><TableRow><TableHead className="w-10"><span className="sr-only">选择报告</span></TableHead><TableHead>文档名称</TableHead><TableHead>审核规则</TableHead><TableHead>审核结果</TableHead><TableHead>文件类型</TableHead><TableHead>发起人</TableHead><TableHead>发起时间</TableHead><TableHead>客户名称</TableHead><TableHead>车型</TableHead><TableHead>零件号</TableHead><TableHead>零件名称</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{current.documents.map(document => {
        const initiator = document.latest_completed?.initiator ?? document.latest_activity?.initiator, rule = document.latest_completed?.rule_title ?? document.latest_activity?.rule_title, at = time(document)
        return <TableRow key={document.document_id} data-state={checked[document.document_id] ? 'selected' : undefined} className="cursor-pointer" onClick={event => {if (event.target instanceof Element && event.currentTarget.contains(event.target) && !event.target.closest('button, a, [role="checkbox"]')) openDocument(document)}}>
        <TableCell><Checkbox aria-label={`选择 ${document.title} 档案 ${document.document_id.split('-').at(-1)}`} checked={Boolean(checked[document.document_id])} disabled={!checked[document.document_id] && (!document.latest_completed || !historyReportReady(document.latest_completed))} onCheckedChange={value => toggle(document, value === true)}/></TableCell>
        <TableCell className="whitespace-normal"><div className="min-w-64 max-w-96 py-1"><button type="button" className="rounded-sm text-left text-primary outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50" onClick={() => openDocument(document)}><FileName name={document.title} colored/></button></div></TableCell>
        <TableCell className="whitespace-normal"><div className="min-w-32 max-w-56">{rule || unset}</div></TableCell>
        <TableCell>{status(document)}</TableCell>
        <TableCell>{document.document_type ? documentTypeLabel(document.document_type) : unset}</TableCell>
        <TableCell>{initiator || unset}</TableCell>
        <TableCell>{at ? <time className="text-muted-foreground tabular-nums" dateTime={at} title={displayTime(at)}>{displayTime(at)}</time> : unset}</TableCell>
        <TableCell>{document.metadata.customer || unset}</TableCell>
        <TableCell>{document.metadata.model || unset}</TableCell>
        <TableCell>{document.metadata.part_number ? <span className="tabular-nums">{document.metadata.part_number}</span> : unset}</TableCell>
        <TableCell>{document.metadata.part || unset}</TableCell>
        <TableCell className="text-right"><Button variant="link" size="sm" aria-label="查看审核记录" title="查看审核记录" onClick={() => openDocument(document)}>查看</Button></TableCell>
      </TableRow>})}</TableBody></Table>
      {!current.documents.length && <div className="grid justify-items-center gap-3 py-12 text-center"><p className="text-sm text-muted-foreground">没有符合条件的文件</p>{applied.length > 0 && <Button variant="outline" size="sm" onClick={clearFilters}>清空条件</Button>}</div>}
      <footer className="space-y-3 border-t px-3 py-3">
        {selection}
        <Pager total={current.total} page={query.page} jump onPage={value => setQuery({...query, page: value})}/>
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
