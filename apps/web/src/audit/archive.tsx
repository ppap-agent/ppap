// Stitch: projects/13261964020862199279/screens/2490ee7a26004f1c9f5cef2e207121c4
import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {ArchiveHistory} from './history'
import {readReportPdf, readReportZip, type AuditReportAdapter, type AuditReportPackage, type AuditReportReference} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {useReportPackage} from './report-actions'
import {ReportPackage} from './report-package'
import {historyReportReady, historyReportReference, resultKey, readArchiveAddress, writeArchiveAddress, type HistoryEntry} from './archive-contract'
import type {OriginalRenderer} from './review-content'
import {sameSnapshot} from './result-projection'
import type {ArchiveDocument, ArchiveQuery, AuditArchiveAdapter} from './archive-contract'

const initialQuery: ArchiveQuery = {search: '', category_id: null, document_type: null, customer: null, initiator: null, from_date: null, through_date: null, page: 1, page_size: 10}

export function AuditArchive({adapter, onUpload, renderOriginal, onOpenTask, reportAdapter, sharingAdapter, active = true}: {adapter: AuditArchiveAdapter; onUpload: () => void; renderOriginal?: OriginalRenderer; onOpenTask: (id: string) => void; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; active?: boolean}) {
  const [query, setQuery] = useState(initialQuery), [search, setSearch] = useState('')
  const [page, setPage] = useState<components['schemas']['AuditArchivePage'] | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [treeOpen, setTreeOpen] = useState(false), [address, setAddress] = useState(() => readArchiveAddress(location.search)), [restore, setRestore] = useState(0)
  const epoch = useRef(0)
  useEffect(() => {if (!active) return; const changed = () => {if (new URLSearchParams(location.search).get('view') !== 'archive') return; setAddress(readArchiveAddress(location.search)); setRestore(value => value + 1)}; changed(); window.addEventListener('popstate', changed); return () => window.removeEventListener('popstate', changed)}, [active])
  function openDocument(document: ArchiveDocument) {setAddress({documentId: document.document_id, reference: null, invalid: false, report: false}); writeArchiveAddress(document.document_id, null)}
  const [checked, setChecked] = useState<Record<string, {entry: HistoryEntry; title: string}>>({})
  const known = useRef(new Map<string, {entry: HistoryEntry; title: string}>())
  const [job, setJob] = useState<AuditReportPackage | null>(null), [downloadError, setDownloadError] = useState(''), [downloading, setDownloading] = useState(false)
  const alive = useRef(true)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  const packaging = useReportPackage(job, setJob, reference => {const item = known.current.get(resultKey(reference)); return Boolean(item && historyReportReady(item.entry) && sameSnapshot(historyReportReference(item.entry), reference))}, reportAdapter)
  const chosen = Object.values(checked)
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
    } catch {if (alive.current) setDownloadError('下载未完成。固定报告集合仍保留，未改用最新版本。')}
    finally {if (url) URL.revokeObjectURL(url); if (alive.current) setDownloading(false)}
  }
  useEffect(() => {
    const run = ++epoch.current
    setLoading(true); setError('')
    void adapter.listDocuments(structuredClone(query)).then(result => {
      if (epoch.current !== run) return
      if (!sameSnapshot(result.query, query) || new Set(result.documents.map(row => row.document_id)).size !== result.documents.length || result.documents.some(row => row.latest_completed && (row.latest_completed.document_id !== row.document_id || row.latest_completed.execution !== 'completed') || row.latest_activity && row.latest_activity.document_id !== row.document_id)) throw new Error('archive_query_mismatch')
      setPage(result)
    }).catch(() => {if (epoch.current === run) setError('文件档案暂时无法读取。未改用其他组织或最新任务结果。')}).finally(() => {if (epoch.current === run) setLoading(false)})
    return () => {epoch.current += 1}
  }, [adapter, query, reload])
  function filter(update: Partial<ArchiveQuery>) {setQuery(current => ({...current, ...update, page: 1}))}
  const current = page && sameSnapshot(page.query, query) ? page : null
  const tree = <nav className="grid gap-1" aria-label="车型与零件"><Button variant={!query.category_id ? 'secondary' : 'ghost'} className="justify-start" onClick={() => {filter({category_id: null}); setTreeOpen(false)}}>全部文件 {page?.document_count ?? '—'}</Button>{page?.categories.map(category => <Collapsible key={category.id} defaultOpen><div className="flex items-center"><CollapsibleTrigger asChild><Button variant="ghost" size="icon" aria-label={`展开或收起 ${category.label}`}>⌄</Button></CollapsibleTrigger><Button variant={query.category_id === category.id ? 'secondary' : 'ghost'} className="min-w-0 flex-1 justify-start" onClick={() => {filter({category_id: category.id}); setTreeOpen(false)}}>{category.label} {category.count}</Button></div><CollapsibleContent><div className="ml-5 grid gap-1">{category.parts.map(part => <Button key={part.id} className="justify-start" variant={query.category_id === part.id ? 'secondary' : 'ghost'} onClick={() => {filter({category_id: part.id}); setTreeOpen(false)}}>{part.label} {part.count}</Button>)}</div></CollapsibleContent></Collapsible>)}</nav>
  const choices = (label: string, value: string | null, options: string[], key: 'document_type' | 'customer' | 'initiator') => <Select value={value || 'all'} onValueChange={value => filter({[key]: value === 'all' ? null : value})}><SelectTrigger aria-label={label}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">{label}</SelectItem>{options.map(option => <SelectItem key={option} value={option}>{option}</SelectItem>)}</SelectContent></Select>
  const status = (document: ArchiveDocument) => <>{document.latest_completed ? <Badge variant="outline">第 {document.latest_completed.sequence} 次审核 · {document.latest_completed.conclusion || '结果未知'}</Badge> : <span>尚无完成审核</span>}{document.latest_activity && ['queued', 'running'].includes(document.latest_activity.execution) && <p className="mt-1 text-xs text-muted-foreground">第 {document.latest_activity.sequence} 次审核中</p>}</>
  if (!active) return null
  if (address.documentId) return <ArchiveHistory key={`${address.documentId}:${restore}`} documentId={address.documentId} document={page?.documents.find(row => row.document_id === address.documentId) || null} initialAddress={address} onNavigate={(reference, report, replace) => writeArchiveAddress(address.documentId, reference, report, replace)} adapter={adapter} renderOriginal={renderOriginal} onBack={() => {setAddress({documentId: null, reference: null, invalid: false, report: false}); writeArchiveAddress(null, null)}} onOpenTask={onOpenTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter}/>
  return <section className="space-y-5" aria-label="文件档案"><header className="flex flex-wrap items-center justify-between gap-3"><div className="space-y-2"><h1 className="text-2xl font-semibold">审核文件</h1><p className="text-muted-foreground">按文档查看历次审核，保留当次原件、规则和报告。</p></div><Button onClick={onUpload}>上传文件</Button></header>
    <Sheet open={treeOpen} onOpenChange={setTreeOpen}><SheetTrigger asChild><Button variant="outline" className="lg:hidden">车型与零件</Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>车型与零件</SheetTitle><SheetDescription>数量按文档统计，不按审核次数累计。</SheetDescription></SheetHeader><div className="overflow-y-auto p-4">{tree}</div></SheetContent></Sheet>
    <div className="grid min-w-0 gap-6 lg:grid-cols-[13rem_minmax(0,1fr)]"><aside className="border-r pr-4 max-lg:hidden">{tree}</aside><div className="min-w-0 space-y-4">
      <form role="search" aria-label="筛选审核文件" className="flex flex-wrap items-end gap-2" onSubmit={event => {event.preventDefault(); filter({search})}}><Input aria-label="搜索文档名" placeholder="搜索文档名" className="max-w-sm" value={search} onChange={event => setSearch(event.target.value)}/><Button type="submit" variant="outline">搜索</Button>{choices('全部文件类型', query.document_type, page?.document_types || [], 'document_type')}{choices('全部客户', query.customer, page?.customers || [], 'customer')}{choices('全部发起人', query.initiator, page?.initiators || [], 'initiator')}<label className="grid gap-1 text-xs text-muted-foreground">开始日期<Input type="date" value={query.from_date || ''} onChange={event => filter({from_date: event.target.value || null})}/></label><label className="grid gap-1 text-xs text-muted-foreground">结束日期<Input type="date" value={query.through_date || ''} onChange={event => filter({through_date: event.target.value || null})}/></label><Button type="button" variant="ghost" onClick={() => {setSearch(''); setQuery(initialQuery)}}>清除筛选</Button><Button type="button" variant="outline" disabled={loading} onClick={() => setReload(value => value + 1)}>刷新档案</Button></form>
      {loading && <p role="status">正在读取文件档案…</p>}{error && <Alert variant="destructive"><AlertTitle>档案读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(value => value + 1)}>重新读取档案</Button></AlertDescription></Alert>}
      {chosen.length > 0 && <section aria-label="已选固定报告" className="space-y-2"><p className="text-xs text-muted-foreground">已选择 {chosen.length} 份报告。选择时固定审核次数；刷新和翻页不会改用最新审核。</p><div className="flex flex-wrap gap-2">{chosen.map(item => <Badge variant="outline" key={resultKey(item.entry.reference)}>{item.title} · 第 {item.entry.sequence} 次审核</Badge>)}</div>{chosen.length > 1 ? <Button disabled={!reportAdapter || packaging.packageBusy || job?.receipt.state === 'pending' || job?.state === 'generating'} onClick={() => void packaging.createPackage(chosen.map(item => historyReportReference(item.entry)!))}>打包所选 {chosen.length} 份历史报告</Button> : <Button disabled={!reportAdapter || downloading} onClick={() => void download()}>下载所选 1 份历史报告</Button>}</section>}
      <ReportPackage job={job} nameFor={reference => known.current.get(resultKey(reference))?.title || '历史文件'} busy={packaging.packageBusy || downloading} onRead={() => void packaging.readPackage()} onDownload={() => void download(true)} onRetry={() => {if (job?.state === 'failed' && job.retry_allowed) void packaging.createPackage(job.reports)}} downloadError={downloadError}/>
      {!job && downloadError && <p role="alert">{downloadError}</p>}
      {current && <><Table aria-label="审核文件档案" className="table-fixed"><TableHeader><TableRow><TableHead className="w-10"><span className="sr-only">选择报告</span></TableHead><TableHead>文件名</TableHead><TableHead className="w-44 max-md:hidden">客户／车型／零件</TableHead><TableHead className="w-44 max-md:hidden">最新完成审核</TableHead><TableHead className="w-28 max-xl:hidden">更新日期</TableHead><TableHead className="w-32 max-md:hidden">操作</TableHead></TableRow></TableHeader><TableBody>{current.documents.map(document => <TableRow key={document.document_id}><TableCell><Checkbox aria-label={`选择 ${document.title} 档案 ${document.document_id.split('-').at(-1)}`} checked={Boolean(checked[document.document_id])} disabled={!checked[document.document_id] && (!document.latest_completed || !historyReportReady(document.latest_completed))} onCheckedChange={value => toggle(document, value === true)}/></TableCell><TableCell className="whitespace-normal"><FileName name={document.title}/><p className="mt-1 text-xs text-muted-foreground">档案 {document.document_id.split('-').at(-1)}</p><div className="mt-2 space-y-2 md:hidden">{status(document)}<Button variant="outline" size="sm" onClick={() => openDocument(document)}>查看审核记录</Button></div></TableCell><TableCell className="whitespace-normal max-md:hidden"><span className="text-xs text-muted-foreground">{[document.metadata.customer, document.metadata.model, document.metadata.part].filter(Boolean).join(' · ') || '其他'}</span></TableCell><TableCell className="whitespace-normal max-md:hidden">{status(document)}</TableCell><TableCell className="max-xl:hidden"><span className="text-xs text-muted-foreground">{document.updated_at.slice(0, 10)}</span></TableCell><TableCell className="max-md:hidden"><Button variant="ghost" size="sm" onClick={() => openDocument(document)}>查看审核记录</Button></TableCell></TableRow>)}</TableBody></Table>{!current.documents.length && <p className="py-8 text-muted-foreground">当前筛选下没有文档。</p>}<footer className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">共 {current.total} 份文档</p><div className="flex items-center gap-2"><span>第 {query.page} 页 · 每页 10 份</span><Button variant="outline" disabled={query.page === 1} onClick={() => setQuery({...query, page: query.page - 1})}>上一页</Button><Button variant="outline" disabled={query.page * 10 >= current.total} onClick={() => setQuery({...query, page: query.page + 1})}>下一页</Button></div></footer></>}
    </div></div>
  </section>
}
