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
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {FileName} from '../journey/file-name'
import {resultConsistencyError, reportReady, reportReference, shareContentFor, sameSnapshot, taskPackageScopeError, packageOutcomeError} from './result-projection'
import {readReportPdf, readReportZip, type AuditReportAdapter} from './report-contract'
import {useReportActions} from './report-actions'
import {ReportPackage, LinkedReportPackage, reportPackageAddress} from './report-package'
import {ReportPreview} from './report-preview'
import {ReportShareDialog} from './report-share'
import {useShareActions} from './share-actions'
import {conclusionLabel, reportShortLabel as labels, resultTone} from './status-labels'
import type {AuditSharingAdapter} from './sharing-contract'

type Task = components['schemas']['AuditTask']

export function AuditReports({task, initialFile, onChoose, onRead, busy, readError, onReadSupport, adapter, onChange, sharingAdapter, headerActions}: {task: Task; initialFile: string; onChoose: (fileId: string) => void; onRead: () => void; busy: boolean; readError: string; onReadSupport: () => void; adapter?: AuditReportAdapter; onChange: (task: Task) => void; sharingAdapter?: AuditSharingAdapter; headerActions?: HTMLElement | null}) {
  const rows = task.files.filter(file => file.selection === 'selected' && file.match?.state !== 'reference')
  function statusFor(row: Task['files'][number]) {
    const report = row.audit?.report
    return resultConsistencyError(task, row) ? '结果待确认' : !report ? '尚未取得结果' : report.state === 'ready' && !reportReady(task, row) ? '产物待确认' : labels[report.state]
  }
  const [open, setOpen] = useState(false), [wide, setWide] = useState(() => window.matchMedia('(min-width: 900px)').matches)
  useEffect(() => {const media = window.matchMedia('(min-width: 900px)'), update = () => setWide(media.matches); media.addEventListener('change', update); return () => media.removeEventListener('change', update)}, [])
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
    const row = selectedReports[0], ref = row ? reportReference(task, row) : null, artifact = row?.audit?.report.artifact
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
  const list = <div className="space-y-2"><div className="flex items-center gap-2 px-3 text-xs text-muted-foreground">{/* Select all reports that can be packed or shared together. */}<Checkbox aria-label="全选可下载报告" title="勾选报告后可打包下载或分享" disabled={!readyRows.length} checked={readyRows.length > 0 && readyRows.every(row => checked.includes(row.file.id)) ? true : checked.length ? 'indeterminate' : false} onCheckedChange={value => setChecked(value === true ? readyRows.map(row => row.file.id) : [])}/>报告（{rows.length}）</div><ul aria-label="逐文件报告清单" className="grid gap-2">{rows.map(row => {
    const ready = reportReady(task, row)
    const status = statusFor(row)
    const duplicates = rows.filter(file => file.file.original_filename === row.file.original_filename)
    const suffix = duplicates.length > 1 ? ` · 同名文件 ${duplicates.findIndex(file => file.file.id === row.file.id) + 1}/${duplicates.length}` : ''
    const outcome = !packageScopeIssue && job && !packageOutcomeError(job) ? job.outcomes?.find(item => sameSnapshot(item.reference, reportReference(task, row))) : undefined
    const choose = () => {onChoose(row.file.id); setOpen(false)}
    return <li key={row.file.id} data-ready={ready || undefined} data-selected={file?.file.id === row.file.id || undefined} className="flex items-start gap-1 rounded-lg border bg-muted/40 p-1 pl-3 text-muted-foreground transition-colors hover:border-primary/40 data-ready:bg-card data-ready:text-foreground data-selected:border-primary data-selected:bg-primary/5">
      <div className="pt-3"><Checkbox aria-label={`选择报告 ${row.file.original_filename}${suffix}`} disabled={!ready} checked={checked.includes(row.file.id)} onCheckedChange={value => setChecked(ids => value === true ? [...ids, row.file.id] : ids.filter(id => id !== row.file.id))}/></div>
      <div className="min-w-0 flex-1"><Button variant="ghost" aria-label={`${row.file.original_filename} ${status}${suffix}`} aria-pressed={file?.file.id === row.file.id} className="h-auto w-full justify-start whitespace-normal text-left" onClick={choose}><span className="grid min-w-0 gap-1.5 py-1.5"><FileName name={row.file.original_filename} colored/><span className="flex flex-wrap items-center gap-2 pl-8 text-xs font-normal">{ready ? <Badge shape="square" variant={resultTone(row.audit?.conclusion)}>{row.audit?.conclusion ? conclusionLabel[row.audit.conclusion] ?? '结果未知' : '结果未知'}</Badge> : <Badge shape="square" variant="outline">{status}</Badge>}{suffix && <span className="text-muted-foreground">{suffix.slice(3)}</span>}</span></span></Button>{outcome?.state === 'failed' && <p className="px-2.5 pb-1.5 text-xs text-destructive">上次打包：{outcome.reason}</p>}</div>
    </li>
  })}</ul></div>
  const packages = packageRequest ? <LinkedReportPackage key={packageRequest} task={task} requestId={packageRequest} adapter={adapter} nameFor={reference => describe(reference).name} busy={downloading} onDownload={job => void downloadZip(job)} onExit={leaveLinkedPackage} downloadError={zipDownloadError}/> : packageScopeIssue ? <Alert variant="destructive"><AlertTitle>ZIP 不属于当前任务</AlertTitle><AlertDescription>{packageScopeIssue} 未读取或下载该包，已有 PDF 不受影响。<Button variant="outline" disabled={busy} onClick={onRead}>重新读取报告状态</Button></AlertDescription></Alert> : <ReportPackage job={job} nameFor={reference => describe(reference).name} busy={actions.packageBusy || downloading} onRead={() => void actions.readPackage()} onDownload={() => void downloadZip()} onRetry={() => {if (job?.state === 'failed' && job.retry_allowed) void actions.createPackage(job.reports)}} downloadError={zipDownloadError}/>
  const count = selectedReports.length, scope = count ? `所选 ${count} 份` : '所选'
  // The visible verbs follow the prototype; hidden words keep each control's full name for assistive tech.
  const download = count > 1 ? selectedZip ? <Button size="sm" variant="outline" disabled={downloading} onClick={() => void downloadZip()}><Download/>下载<span className="sr-only">所选</span>报告<span className="sr-only"> ZIP</span></Button> : <Button size="sm" variant="outline" disabled={!actions.canCreatePackage(selectedReferences)} onClick={() => {if (packageRequest) leaveLinkedPackage(); void actions.createPackage(selectedReferences)}}><Package/>打包<span className="sr-only">{scope}</span>报告</Button> : <Button size="sm" variant="outline" disabled={!count || !adapter || downloading} onClick={() => void downloadSelected()}><Download/>下载<span className="sr-only">{scope}</span>报告</Button>
  const batch = <div className="flex flex-wrap items-center gap-2" aria-label="所选报告操作"><Button size="sm" disabled={!count || !sharingAdapter} onClick={event => openShare(event, selectedReferences)}><Share2/>分享<span className="sr-only">所选</span></Button>{download}{count ? <><span className="text-xs text-muted-foreground">已选 {count} 份</span><Button variant="ghost" size="sm" onClick={() => setChecked([])}>取消选择</Button></> : <span className="text-xs text-muted-foreground">勾选报告后可批量分享或下载</span>}</div>
  const files = <div className="space-y-3">{batch}{wide && !packageRequest && packages}{list}</div>
  const controls = <div className="flex items-center gap-2"><Sheet open={open} onOpenChange={setOpen}><SheetTrigger asChild><Button className="min-[900px]:hidden" variant="outline" size="sm">文件报告 · {rows.length} 份</Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>文件报告</SheetTitle><SheetDescription className="sr-only">选择需要查看或下载的报告</SheetDescription></SheetHeader><div className="min-h-0 flex-1 overflow-y-auto p-4">{!wide && files}</div></SheetContent></Sheet><Button variant="ghost" size="icon" aria-label="读取报告状态" title="刷新报告状态" disabled={busy} onClick={onRead}><RefreshCw className={busy ? 'animate-spin motion-reduce:animate-none' : ''}/></Button></div>
  return <section className="space-y-4" aria-label="逐文件审核报告"><h1 className={headerActions ? "sr-only" : "text-xl font-semibold"}>审核报告</h1>{headerActions ? createPortal(controls, headerActions) : controls}
    {!wide && !open && count > 0 && batch}{(!wide || packageRequest) && packages}
    {readError && <Alert variant="destructive"><AlertTitle>报告状态暂时无法读取</AlertTitle><AlertDescription><p>{readError}</p><Button variant="outline" size="sm" onClick={onReadSupport}>联系支持</Button></AlertDescription></Alert>}
    {pdfDownloadError && <Alert variant="destructive"><AlertTitle>PDF 下载未完成</AlertTitle><AlertDescription>{pdfDownloadError}</AlertDescription></Alert>}
    <div className="h-[calc(100dvh-6rem)] min-h-[32rem] max-[899px]:h-auto"><ResizablePanelGroup orientation="horizontal" disabled={!wide} className="max-[899px]:block!" aria-label="报告选择与阅读"><ResizablePanel defaultSize="30%" minSize={wide ? '260px' : '0%'} maxSize="45%" className="min-w-0 max-[899px]:hidden!"><aside className="h-full min-w-0 overflow-y-auto pr-4">{wide && files}</aside></ResizablePanel>{wide && <ResizableHandle withHandle/>}<ResizablePanel defaultSize="70%" minSize={wide ? '360px' : '0%'} className="min-w-0"><div className="h-full min-w-0 min-[900px]:pl-5">{file ? <ReportPreview verification={file.audit?.checker_verification} name={file.file.original_filename} rule={file.audit?.rule.title || '—'} ruleVersion={file.audit?.rule.id} reference={reference} report={report} ready={ready} issue={issue} adapter={adapter} actions={<Button variant="ghost" size="sm" disabled={!sharingAdapter || !ready && !task.report_share} onClick={event => openShare(event, ready && reference ? [reference] : [])}><Share2/>分享</Button>}>{reference && report && (report.generation_request?.state === 'pending' || report.state === 'failed') && <div className="space-y-2">{report.generation_request?.state === 'pending' ? <><p role="status" data-request-id={report.generation_request.request_id}>{report.generation_request.reason}</p><Button variant="outline" disabled={actions.busy.includes(reference.report_id)} onClick={() => void actions.read(reference)}>读取生成结果</Button></> : <><Button variant="outline" disabled={!adapter || !report.regenerate_allowed || actions.busy.includes(reference.report_id)} onClick={() => void actions.regenerate(reference)}>重生成同一结果</Button>{(report.regenerate_reason || !report.regenerate_allowed) && <p className="text-xs text-muted-foreground">{report.regenerate_reason || '暂不能重新生成报告。'}</p>}</>}</div>}</ReportPreview> : <p>{rows.length ? '所选报告不可用，请从清单中重新选择。' : '没有进入审核的文件，未开始审核，也没有报告。'}</p>}</div></ResizablePanel></ResizablePanelGroup></div>
    {shareScope !== null && sharingAdapter && <ReportShareDialog key={JSON.stringify(shareScope)} open={sharingOpen} share={task.report_share} contentFor={contentFor} describe={describe} reports={shareScope} adapter={sharingAdapter} actions={sharing} trigger={shareTrigger.current} onClose={() => setSharingOpen(false)}/>}
  </section>
}
