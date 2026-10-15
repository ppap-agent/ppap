// Stitch: projects/13261964020862199279/screens/ad9a81d6958e49eea105a9ea887cc3cf
import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {FileName} from '../journey/file-name'
import {resultConsistencyError, reportReady, reportReference, shareContentFor, sameSnapshot} from './result-projection'
import {readReportPdf, type AuditReportAdapter} from './report-contract'
import {useReportActions} from './report-actions'
import {ReportPackage} from './report-package'
import {ReportPreview} from './report-preview'
import {ReportShareDialog} from './report-share'
import {useShareActions} from './share-actions'
import type {AuditSharingAdapter} from './sharing-contract'

type Task = components['schemas']['AuditTask']
const labels = {not_eligible: '无正式报告', pending: '待生成', generating: '生成中', ready: '就绪', failed: '生成失败'}

export function AuditReports({task, initialFile, onBack, onChoose, onRead, busy, readError, adapter, onChange, sharingAdapter}: {task: Task; initialFile: string; onBack: () => void; onChoose: (fileId: string) => void; onRead: () => void; busy: boolean; readError: string; adapter?: AuditReportAdapter; onChange: (task: Task) => void; sharingAdapter?: AuditSharingAdapter}) {
  const rows = task.files.filter(file => file.selection === 'selected')
  function statusFor(row: Task['files'][number]) {
    const report = row.audit?.report
    return resultConsistencyError(task, row) ? '结果待核对' : !report ? '尚未取得结果' : report.state === 'ready' && !reportReady(task, row) ? '产物待核对' : labels[report.state]
  }
  const [selected, setSelected] = useState(initialFile), [open, setOpen] = useState(false)
  const [checked, setChecked] = useState<string[]>([])
  const file = rows.find(row => row.file.id === selected) ?? (selected ? undefined : rows[0])
  const report = file?.audit?.report
  const issue = file ? resultConsistencyError(task, file) : ''
  const ready = Boolean(file && reportReady(task, file))
  const reference = file ? reportReference(task, file) : null
  const actions = useReportActions(task, onChange, adapter)
  const currentTask = useRef(task); currentTask.current = task
  const contentFor = (reports: components['schemas']['AuditReportReference'][], mode: 'link' | 'file') => shareContentFor(currentTask.current, reports, mode)
  const sharing = useShareActions(task.report_share, job => {const next = {...currentTask.current, report_share: job}; currentTask.current = next; onChange(next)}, contentFor, sharingAdapter)
  const describe = (reference: components['schemas']['AuditReportReference']) => {const row = task.files.find(row => sameSnapshot(reportReference(task, row), reference)); return {name: row?.file.original_filename || '历史文件', conclusion: row?.audit?.conclusion || '结论待核对'}}
  const [shareScope, setShareScope] = useState<components['schemas']['AuditReportReference'][] | null>(null)
  const shareTrigger = useRef<HTMLButtonElement | null>(null)
  const downloads = useRef<string[]>([]), alive = useRef(true)
  const [pdfDownloadError, setPdfDownloadError] = useState(''), [zipDownloadError, setZipDownloadError] = useState(''), [downloading, setDownloading] = useState(false)
  useEffect(() => {alive.current = true; return () => {alive.current = false; downloads.current.forEach(url => URL.revokeObjectURL(url))}}, [])
  const selectedReports = rows.filter(row => checked.includes(row.file.id) && reportReady(task, row))
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
  async function downloadZip() {
    const job = task.report_package, artifact = job?.artifact
    if (!adapter || !job || !artifact || job.state !== 'ready' || downloading) return
    setDownloading(true); setZipDownloadError('')
    try {
      const blob = await adapter.readPackageFile(structuredClone(job))
      if (blob.type !== 'application/zip' || blob.size !== artifact.byte_size || (await blob.slice(0, 2).text()) !== 'PK') throw new Error('invalid_zip_artifact')
      saveBlob(blob, artifact.filename)
    } catch {if (alive.current) setZipDownloadError('ZIP 暂时无法下载。已就绪 PDF 保留，只需重试下载。')}
    finally {if (alive.current) setDownloading(false)}
  }
  const list = <ItemGroup aria-label="逐文件报告清单">{rows.map(row => {
    const ready = reportReady(task, row)
    const status = statusFor(row)
    const duplicates = rows.filter(file => file.file.original_filename === row.file.original_filename)
    const suffix = duplicates.length > 1 ? ` · 同名文件 ${duplicates.findIndex(file => file.file.id === row.file.id) + 1}/${duplicates.length}` : ''
    return <Item role="listitem" key={row.file.id} size="xs" className="items-start">
      <Checkbox aria-label={`选择报告 ${row.file.original_filename}${suffix}`} disabled={!ready} checked={checked.includes(row.file.id)} onCheckedChange={value => setChecked(ids => value === true ? [...ids, row.file.id] : ids.filter(id => id !== row.file.id))}/>
      <ItemContent><Button variant={file?.file.id === row.file.id ? 'secondary' : 'ghost'} aria-label={`${row.file.original_filename}-审核报告 ${status}${suffix}`} aria-pressed={file?.file.id === row.file.id} className="h-auto justify-start whitespace-normal text-left" onClick={() => {setSelected(row.file.id); onChoose(row.file.id); setOpen(false)}}>
        <span className="grid min-w-0 gap-2"><FileName name={`${row.file.original_filename}-审核报告`} format="pdf"/><span className="text-xs">{status}{suffix}</span>{row.audit?.report.generated_at && <span className="text-xs">{row.audit.report.generated_at.replace('T', ' ')}</span>}</span>
      </Button></ItemContent>
    </Item>
  })}</ItemGroup>
  return <section className="@container space-y-5" aria-label="逐文件审核报告"><header className="space-y-3"><Button variant="ghost" aria-label="返回审核结果" onClick={onBack}>← 返回审核结果</Button><h1 className="text-2xl font-semibold">审核报告</h1><p className="text-muted-foreground">逐文件查看同一次审核的报告。报告生成失败不改变审核结果，也不触发重审。</p></header>
    {readError && <Alert variant="destructive"><AlertTitle>报告状态暂时无法读取</AlertTitle><AlertDescription>{readError}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center gap-2"><Sheet open={open} onOpenChange={setOpen}><SheetTrigger asChild><Button className="@3xl:hidden" variant="outline">文件报告 · {rows.length} 份</Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>文件报告</SheetTitle><SheetDescription>当前预览与批量勾选分别保存；只有就绪报告可勾选。</SheetDescription></SheetHeader><div className="overflow-y-auto p-4">{list}</div></SheetContent></Sheet>{selectedReports.length > 1 ? <Button disabled={!adapter || actions.packageBusy || task.report_package?.receipt.state === 'pending' || task.report_package?.state === 'generating'} onClick={() => void actions.createPackage(selectedReports.map(row => reportReference(task, row)!))}>打包所选 {selectedReports.length} 份报告</Button> : selectedReports.length === 1 && <Button disabled={!adapter || downloading} onClick={() => void downloadSelected()}>下载所选 1 份报告</Button>}<Button variant="outline" disabled={busy} onClick={onRead}>{busy ? '正在读取报告状态…' : '读取报告状态'}</Button><Button variant="outline" disabled={!sharingAdapter || !selectedReports.length && !ready && !task.report_share} onClick={event => {shareTrigger.current = event.currentTarget; setShareScope(selectedReports.length ? selectedReports.map(row => reportReference(task, row)!) : ready && reference ? [reference] : [])}}>分享</Button>{!sharingAdapter && <span className="text-xs text-muted-foreground">分享接口尚未接入。</span>}</div>
    {pdfDownloadError && <Alert variant="destructive"><AlertTitle>PDF 下载未完成</AlertTitle><AlertDescription>{pdfDownloadError}</AlertDescription></Alert>}
    <ReportPackage job={task.report_package} nameFor={reference => describe(reference).name} busy={actions.packageBusy || downloading} onRead={() => void actions.readPackage()} onDownload={() => void downloadZip()} onRetry={() => {if (task.report_package?.state === 'failed' && task.report_package.retry_allowed) void actions.createPackage(task.report_package.reports)}} downloadError={zipDownloadError}/>
    <div className="grid min-w-0 gap-6 @3xl:grid-cols-[16rem_minmax(0,1fr)]"><aside className="hidden min-w-0 border-r pr-4 @3xl:block">{list}</aside>{file ? <ReportPreview name={file.file.original_filename} rule={file.audit?.rule.title || '—'} reference={reference} report={report} ready={ready} issue={issue} adapter={adapter}>{reference && report && <div className="space-y-2">{report.generation_request?.state === 'pending' ? <><p role="status">{report.generation_request.reason}</p><p className="break-all text-xs text-muted-foreground">生成请求：{report.generation_request.request_id}</p><Button variant="outline" disabled={actions.busy.includes(reference.report_id)} onClick={() => void actions.read(reference)}>读取生成结果</Button></> : report.state === 'failed' && <><Button variant="outline" disabled={!adapter || !report.regenerate_allowed || actions.busy.includes(reference.report_id)} onClick={() => void actions.regenerate(reference)}>重生成同一结果</Button><p className="text-xs text-muted-foreground">{report.regenerate_reason || '服务未提供重生成许可；未推定收费政策。'}</p></>}</div>}</ReportPreview> : <p>{rows.length ? '所选报告不在当前任务的冻结范围内。请从文件报告清单选择，不自动打开另一份报告。' : '没有进入审核的文件，未开始审核，也没有报告。'}</p>}</div>
    {shareScope !== null && sharingAdapter && <ReportShareDialog share={task.report_share} contentFor={contentFor} describe={describe} reports={shareScope} adapter={sharingAdapter} actions={sharing} trigger={shareTrigger.current} onClose={() => setShareScope(null)}/>}
  </section>
}
