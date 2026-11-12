// Stitch: projects/13261964020862199279/screens/ad9a81d6958e49eea105a9ea887cc3cf
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Download, Package, RefreshCw, Share2} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {ResizableHandle, ResizablePanel, ResizablePanelGroup} from '../components/ui/resizable'
import {FileName} from '../journey/file-name'
import {auditReportFilename, resultConsistencyError, reportReady, reportReference, shareContentFor, sameSnapshot, taskPackageScopeError, packageOutcomeError} from './result-projection'
import {readReportPdf, readReportZip, type AuditReportAdapter} from './report-contract'
import {useReportActions} from './report-actions'
import {ReportPackage, LinkedReportPackage, reportPackageAddress} from './report-package'
import {ReportPreview} from './report-preview'
import {ReportShareDialog} from './report-share'
import {useShareActions} from './share-actions'
import {conclusionLabel, documentTypeLabel, reportShortLabel as labels, resultTone} from './status-labels'
import type {AuditSharingAdapter} from './sharing-contract'

type Task = components['schemas']['AuditTask']

const reportDay = (value: string) => {
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` : ''
}

export function AuditReports({task, initialFile, onChoose, onRead, busy, readError, onReadSupport, adapter, onChange, sharingAdapter, headerActions}: {task: Task; initialFile: string; onChoose: (fileId: string) => void; onRead: () => void; busy: boolean; readError: string; onReadSupport: () => void; adapter?: AuditReportAdapter; onChange: (task: Task) => void; sharingAdapter?: AuditSharingAdapter; headerActions?: HTMLElement | null}) {
  const rows = task.files.filter(file => file.selection === 'selected' && file.match?.state !== 'reference')
  function statusFor(row: Task['files'][number]) {
    const report = row.audit?.report
    return resultConsistencyError(task, row) ? '结果待确认' : !report ? '尚未取得结果' : report.state === 'ready' && !reportReady(task, row) ? '产物待确认' : labels[report.state]
  }
  const [packageRequest, setPackageRequest] = useState(() => new URL(location.href).searchParams.get('packageRequest'))
  function leaveLinkedPackage() {setPackageRequest(null); setZipDownloadError(''); reportPackageAddress(task.id, null)}
  const [checked, setChecked] = useState<string[]>([])
  const file = rows.find(row => row.file.id === initialFile) ?? (initialFile ? undefined : rows[0])
  const report = file?.audit?.report
  const issue = file ? resultConsistencyError(task, file) : ''
  const ready = Boolean(file && reportReady(task, file))
  const reference = file ? reportReference(task, file) : null
  const packageScopeIssue = taskPackageScopeError(task)
  const actions = useReportActions(task, onChange, adapter)
  const currentTask = useRef(task); currentTask.current = task
  const contentFor = (reports: components['schemas']['AuditReportReference'][], mode: 'link' | 'file') => shareContentFor(currentTask.current, reports, mode)
  const sharing = useShareActions(task.report_share, job => {const next = {...currentTask.current, report_share: job}; currentTask.current = next; onChange(next)}, input => sameSnapshot(contentFor(input.reports, input.content.kind === 'link' ? 'link' : 'file'), input.content), sharingAdapter)
  const describe = (reference: components['schemas']['AuditReportReference']) => {const row = task.files.find(row => sameSnapshot(reportReference(task, row), reference)); return {name: row?.file.original_filename || '历史文件', conclusion: row?.audit?.conclusion ? conclusionLabel[row.audit.conclusion] ?? '结论待确认' : '结论待确认'}}
  const [shareScope, setShareScope] = useState<components['schemas']['AuditReportReference'][] | null>(null)
  const [sharingOpen, setSharingOpen] = useState(false)
  const shareTrigger = useRef<HTMLButtonElement | null>(null)
  const downloads = useRef<string[]>([]), alive = useRef(true)
  const [pdfDownloadError, setPdfDownloadError] = useState(''), [zipDownloadError, setZipDownloadError] = useState(''), [downloading, setDownloading] = useState(false)
  useEffect(() => {alive.current = true; return () => {alive.current = false; downloads.current.forEach(url => URL.revokeObjectURL(url))}}, [])
  const selectedReports = rows.filter(row => checked.includes(row.file.id) && reportReady(task, row))
  const selectedReferences = selectedReports.map(row => reportReference(task, row)!)
  const job = task.report_package
  const selectedZip = !packageScopeIssue && job?.state === 'ready' && job.receipt.state === 'accepted' && !packageOutcomeError(job) && job.artifact && job.reports.length === selectedReferences.length && job.reports.every(reference => selectedReferences.some(selected => sameSnapshot(reference, selected)))
  function openShare(event: React.MouseEvent<HTMLButtonElement>, references: components['schemas']['AuditReportReference'][]) {shareTrigger.current = event.currentTarget; setShareScope(references); setSharingOpen(true)}
  function saveBlob(blob: Blob, name: string) {
    if (!alive.current) return
    const url = URL.createObjectURL(blob); downloads.current.push(url)
    const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  }
  async function downloadSelected() {
    const row = selectedReports.length ? selectedReports[0] : ready ? file : undefined, ref = row ? reportReference(task, row) : null, artifact = row?.audit?.report.artifact
    if (!adapter || !ref || !artifact || downloading) return
    setDownloading(true); setPdfDownloadError('')
    try {saveBlob(await readReportPdf(adapter, ref, artifact), artifact.filename)}
    catch {if (alive.current) setPdfDownloadError('所选 PDF 读取失败；报告生成状态未改变，可以重试下载。')}
    finally {if (alive.current) setDownloading(false)}
  }
  async function downloadZip(job = task.report_package) {
    const artifact = job?.artifact
    if (taskPackageScopeError({...task, report_package: job}) || !adapter || !job || !artifact || job.state !== 'ready' || downloading) return
    setDownloading(true); setZipDownloadError('')
    try {
      const blob = await readReportZip(adapter, job)
      saveBlob(blob, artifact.filename)
    } catch {if (alive.current) setZipDownloadError('ZIP 暂时无法下载。已就绪 PDF 保留，只需重试下载。')}
    finally {if (alive.current) setDownloading(false)}
  }
  const readyRows = rows.filter(row => reportReady(task, row))
  // Each report is a card; reports without a ready PDF stay visible but greyed and cannot be selected.
  const list = <div className="space-y-2"><div className="flex items-center gap-2 px-1 text-xs text-muted-foreground">{/* Select all reports that can be packed or shared together. */}<Checkbox aria-label="全选可下载报告" title="勾选报告后可打包下载或分享" disabled={!readyRows.length} checked={readyRows.length > 0 && readyRows.every(row => checked.includes(row.file.id)) ? true : checked.length ? 'indeterminate' : false} onCheckedChange={value => setChecked(value === true ? readyRows.map(row => row.file.id) : [])}/>全选</div><ul aria-label="逐文件报告清单" className="grid gap-2">{rows.map(row => {
    const ready = reportReady(task, row)
    const status = statusFor(row)
    const duplicates = rows.filter(file => file.file.original_filename === row.file.original_filename)
    const suffix = duplicates.length > 1 ? ` · 同名文件 ${duplicates.findIndex(file => file.file.id === row.file.id) + 1}/${duplicates.length}` : ''
    const outcome = !packageScopeIssue && job && !packageOutcomeError(job) ? job.outcomes?.find(item => sameSnapshot(item.reference, reportReference(task, row))) : undefined
    const choose = () => onChoose(row.file.id)
    const type = row.match?.document_type ? documentTypeLabel(row.match.document_type) : '', generated = row.audit?.report.generated_at ? reportDay(row.audit.report.generated_at) : ''
    return <li key={row.file.id} data-ready={ready || undefined} data-selected={file?.file.id === row.file.id || undefined} className="flex items-start gap-2 rounded-sm border bg-muted/40 py-2.5 pr-2 pl-3 text-muted-foreground transition-colors hover:border-primary-line data-ready:bg-card data-ready:text-foreground data-selected:border-primary data-selected:bg-primary-soft">
      <div className="pt-0.5"><Checkbox aria-label={`选择报告 ${row.file.original_filename}${suffix}`} disabled={!ready} checked={checked.includes(row.file.id)} onCheckedChange={value => setChecked(ids => value === true ? [...ids, row.file.id] : ids.filter(id => id !== row.file.id))}/></div>
      <div className="min-w-0 flex-1"><button type="button" aria-label={`${row.file.original_filename} ${status}${suffix}`} aria-pressed={file?.file.id === row.file.id} className="flex w-full items-start gap-2 rounded-sm text-left focus-visible:outline-ring" onClick={choose}>
        <span className="min-w-0 flex-1 space-y-1"><span className={`block text-sm leading-relaxed [overflow-wrap:anywhere] ${ready ? 'text-primary' : ''}`}>{row.audit?.report.artifact?.filename || auditReportFilename(row.file.original_filename)}</span><span className="block text-xs text-muted-foreground">{[type || '类型未定', generated, suffix.slice(3)].filter(Boolean).join(' · ')}</span></span>
        {ready ? <Badge shape="square" variant={resultTone(row.audit?.conclusion)}>{row.audit?.conclusion ? conclusionLabel[row.audit.conclusion] ?? '结果未知' : '结果未知'}</Badge> : <Badge shape="square" variant="outline">{status}</Badge>}
      </button>{outcome?.state === 'failed' && <p className="pt-1 text-xs text-destructive">上次打包：{outcome.reason}</p>}</div>
    </li>
  })}</ul></div>
  const packages = packageRequest ? <LinkedReportPackage key={packageRequest} task={task} requestId={packageRequest} adapter={adapter} nameFor={reference => describe(reference).name} busy={downloading} onDownload={job => void downloadZip(job)} onExit={leaveLinkedPackage} downloadError={zipDownloadError}/> : packageScopeIssue ? <Alert variant="destructive"><AlertTitle>ZIP 不属于当前任务</AlertTitle><AlertDescription>{packageScopeIssue} 未读取或下载该包，已有 PDF 不受影响。<Button variant="outline" disabled={busy} onClick={onRead}>重新读取报告状态</Button></AlertDescription></Alert> : <ReportPackage job={job} nameFor={reference => describe(reference).name} busy={actions.packageBusy || downloading} onRead={() => void actions.readPackage()} onDownload={() => void downloadZip()} onRetry={() => {if (job?.state === 'failed' && job.retry_allowed) void actions.createPackage(job.reports)}} downloadError={zipDownloadError}/>
  const count = selectedReports.length, scope = count ? `所选 ${count} 份` : '所选'
  // Without a selection 分享 and 下载报告 act on the report being read, as in the prototype.
  const current = ready && reference ? [reference] : []
  const download = count > 1 ? selectedZip ? <Button size="sm" variant="outline" disabled={downloading} onClick={() => void downloadZip()}><Download/>下载<span className="sr-only">所选</span>报告<span className="sr-only"> ZIP</span></Button> : <Button size="sm" variant="outline" disabled={!actions.canCreatePackage(selectedReferences)} onClick={() => {if (packageRequest) leaveLinkedPackage(); void actions.createPackage(selectedReferences)}}><Package/>打包<span className="sr-only">{scope}</span>报告</Button>
    : <Button size="sm" variant="outline" disabled={!(count ? selectedReports[0] : ready && file) || !adapter || downloading} onClick={() => void downloadSelected()}><Download/>下载{count ? <span className="sr-only">{scope}</span> : null}报告</Button>
  const batch = <div className="space-y-2" aria-label="所选报告操作"><div className="flex flex-wrap items-center gap-2"><Button size="sm" disabled={!sharingAdapter || !count && !current.length && !task.report_share} onClick={event => openShare(event, count ? selectedReferences : current)}><Share2/>分享{count ? <span className="sr-only">所选</span> : null}</Button>{download}</div>{count > 0 && <div className="flex items-center gap-2 text-xs text-muted-foreground">已选 {count} 份<Button variant="ghost" size="xs" onClick={() => setChecked([])}>取消选择</Button></div>}</div>
  const files = <div className="space-y-3"><div className="flex items-center justify-between gap-2"><h1 className="text-base font-semibold">审核报告</h1><Badge shape="square" variant="outline">{rows.length} 份</Badge></div>{batch}{!packageRequest && packages}{list}</div>
  const controls = <div className="flex items-center gap-2"><Button variant="ghost" size="icon" aria-label="读取报告状态" title="刷新报告状态" disabled={busy} onClick={onRead}><RefreshCw className={busy ? 'animate-spin motion-reduce:animate-none' : ''}/></Button></div>
  return <section className="space-y-4" aria-label="逐文件审核报告">{headerActions ? createPortal(controls, headerActions) : controls}
    {packageRequest && packages}
    {readError && <Alert variant="destructive"><AlertTitle>报告状态暂时无法读取</AlertTitle><AlertDescription><p>{readError}</p><Button variant="outline" size="sm" onClick={onReadSupport}>联系支持</Button></AlertDescription></Alert>}
    {pdfDownloadError && <Alert variant="destructive"><AlertTitle>PDF 下载未完成</AlertTitle><AlertDescription>{pdfDownloadError}</AlertDescription></Alert>}
    <div className="h-full min-h-[32rem]"><ResizablePanelGroup orientation="horizontal" aria-label="报告选择与阅读"><ResizablePanel defaultSize="30%" minSize="260px" maxSize="45%" className="min-w-0"><div className="h-full pr-2"><aside className="h-full min-w-0 overflow-y-auto rounded-md border bg-card p-3.5">{files}</aside></div></ResizablePanel><ResizableHandle withHandle/><ResizablePanel defaultSize="70%" minSize="360px" className="min-w-0"><div className="h-full min-w-0 pl-2">{file ? <ReportPreview verification={file.audit?.checker_verification} name={file.file.original_filename} rule={file.audit?.rule.title || '—'} ruleVersion={file.audit?.rule.id} reference={reference} report={report} ready={ready} issue={issue} adapter={adapter}>{reference && report && (report.generation_request?.state === 'pending' || report.state === 'failed') && <div className="space-y-2">{report.generation_request?.state === 'pending' ? <><p role="status" data-request-id={report.generation_request.request_id}>{report.generation_request.reason}</p><Button variant="outline" disabled={actions.busy.includes(reference.report_id)} onClick={() => void actions.read(reference)}>读取生成结果</Button></> : <><Button variant="outline" disabled={!adapter || !report.regenerate_allowed || actions.busy.includes(reference.report_id)} onClick={() => void actions.regenerate(reference)}>重生成同一结果</Button>{(report.regenerate_reason || !report.regenerate_allowed) && <p className="text-xs text-muted-foreground">{report.regenerate_reason || '暂不能重新生成报告。'}</p>}</>}</div>}</ReportPreview> : <p className="rounded-md border bg-card p-4">{rows.length ? '所选报告不可用，请从清单中重新选择。' : '没有进入审核的文件，未开始审核，也没有报告。'}</p>}</div></ResizablePanel></ResizablePanelGroup></div>
    {shareScope !== null && sharingAdapter && <ReportShareDialog key={JSON.stringify(shareScope)} open={sharingOpen} share={task.report_share} contentFor={contentFor} describe={describe} reports={shareScope} adapter={sharingAdapter} actions={sharing} trigger={shareTrigger.current} onClose={() => setSharingOpen(false)}/>}
  </section>
}
