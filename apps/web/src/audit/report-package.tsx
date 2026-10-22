import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {sameSnapshot, packageOutcomeError, taskPackageScopeError, reportReference} from './result-projection'
import {readTaskReportPackage, type AuditReportAdapter} from './report-contract'
import {useReportPackage} from './report-actions'
import {writeAuditAddress} from './routes'

type Job = components['schemas']['AuditReportPackage']
type Reference = components['schemas']['AuditReportReference']
type Task = components['schemas']['AuditTask']

export function reportPackageAddress(taskId: string, requestId: string | null) {
  const url = new URL(location.href)
  if (requestId) url.searchParams.set('packageRequest', requestId); else url.searchParams.delete('packageRequest')
  writeAuditAddress(url)
  if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: taskId, report: url.searchParams.get('report'), packageRequest: requestId}, location.origin)
}

export function LinkedReportPackage({task, requestId, adapter, nameFor, busy, onDownload, onExit, downloadError}: {task: Task; requestId: string; adapter?: AuditReportAdapter; nameFor: (reference: Reference) => string; busy: boolean; onDownload: (job: Job) => void; onExit: () => void; downloadError: string}) {
  const [job, setJob] = useState<Job | null>(null), [reading, setReading] = useState(false), [error, setError] = useState('')
  const epoch = useRef(0), latest = useRef(task); latest.current = task
  const scopeError = taskPackageScopeError({...task, report_package: job})
  function commit(next: Job) {epoch.current += 1; setReading(false); setJob(next); setError(''); reportPackageAddress(task.id, next.receipt.request_id)}
  const actions = useReportPackage(job, commit, reference => latest.current.files.some(row => sameSnapshot(reportReference(latest.current, row), reference)), adapter, scopeError)
  async function read() {
    if (!adapter) {setError('ZIP 服务暂不可用，已有 PDF 不受影响。'); return}
    const request = ++epoch.current
    setReading(true); setError('')
    try {const next = await readTaskReportPackage(adapter, latest.current, requestId); if (request === epoch.current) setJob(next)}
    catch {if (request === epoch.current) setError('未能读取此打包请求。请重新读取，或返回报告中心；不会改用另一个 ZIP。')}
    finally {if (request === epoch.current) setReading(false)}
  }
  useEffect(() => {void read(); return () => {epoch.current += 1}}, [requestId, adapter])
  return <section aria-label="通知中的 ZIP" className="space-y-3"><Button variant="ghost" onClick={onExit}>返回当前报告中心</Button>{reading && <p role="status">正在读取此 ZIP…</p>}{(error || scopeError) ? <Alert variant="destructive"><AlertTitle>此 ZIP 暂不可用</AlertTitle><AlertDescription>{error || scopeError}<Button variant="outline" disabled={reading} onClick={() => void read()}>重新读取此 ZIP</Button></AlertDescription></Alert> : <ReportPackage job={job} nameFor={nameFor} busy={busy || reading || actions.packageBusy} onRead={() => void actions.readPackage()} onDownload={() => {if (job) onDownload(job)}} onRetry={() => {if (job) void actions.createPackage(job.reports)}} downloadError={downloadError}/>}</section>
}

export function ReportPackage({job, nameFor, busy, onRead, onDownload, onRetry, downloadError}: {job?: Job | null; nameFor: (reference: Reference) => string; busy: boolean; onRead: () => void; onDownload: () => void; onRetry: () => void; downloadError: string}) {
  if (!job) return null
  const known = job.receipt.state !== 'pending'
  const issue = packageOutcomeError(job)
  const labels = {pending: '等待打包', generating: '打包中', ready: 'ZIP 已就绪', failed: 'ZIP 打包失败'}
  const ready = !issue && known && job.state === 'ready'
  const title = issue ? 'ZIP 状态待核对' : known ? labels[job.state] : 'ZIP 受理结果未知'
  const error = downloadError && <Alert variant="destructive"><AlertTitle>ZIP 下载未完成</AlertTitle><AlertDescription>{downloadError}</AlertDescription></Alert>
  const controls = <div className="flex flex-wrap gap-2">{ready ? <Button variant="outline" disabled={busy} onClick={onDownload}>下载已就绪 ZIP</Button> : <Button variant="outline" size="sm" disabled={busy} onClick={onRead}>{busy ? '正在读取 ZIP 状态…' : '读取 ZIP 状态'}</Button>}{!issue && known && job.state === 'failed' && <Button variant="outline" size="sm" disabled={busy || !job.retry_allowed} onClick={onRetry}>重试同一报告集合打包</Button>}</div>
  return <section aria-label="ZIP 工作" className="min-w-0 space-y-2">
    <div className="flex flex-wrap items-center gap-2">{!ready && <h2 className="text-xs font-medium">{title}</h2>}<Sheet><SheetTrigger asChild><Button variant="ghost" size="sm">打包记录</Button></SheetTrigger><SheetContent className="sm:max-w-xl"><SheetHeader><SheetTitle>打包记录</SheetTitle><SheetDescription className="sr-only">此打包请求创建时固定的报告集合</SheetDescription></SheetHeader><div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4"><h2 className="font-medium">{title}</h2><Table aria-label="固定打包报告"><TableHeader><TableRow><TableHead>报告</TableHead><TableHead className="w-24">准备状态</TableHead></TableRow></TableHeader><TableBody>{job.reports.map(reference => {
      const outcome = issue ? undefined : job.outcomes?.find(row => sameSnapshot(row.reference, reference))
      return <TableRow key={JSON.stringify(reference)} data-report-reference={JSON.stringify(reference)}><TableCell className="whitespace-normal"><FileName name={outcome?.filename || `${nameFor(reference)}-审核报告`} format="pdf"/>{outcome?.reason && <p className="mt-2 text-sm text-destructive">{outcome.reason}</p>}</TableCell><TableCell>{outcome?.state === 'prepared' ? '完成' : <Badge variant={outcome?.state === 'failed' ? 'destructive' : 'outline'}>{outcome?.state === 'failed' ? '失败' : issue ? '待核对' : !job.outcomes ? '未提供逐项状态' : '尚未确认'}</Badge>}</TableCell></TableRow>
    })}</TableBody></Table>{controls}{error}</div></SheetContent></Sheet></div>
    {!ready && job.reason && <p role="status" className="text-xs text-muted-foreground">{job.reason}</p>}
    {issue && <Alert variant="destructive"><AlertTitle>ZIP 明细需要核对</AlertTitle><AlertDescription>{issue} 请读取原打包请求的状态。</AlertDescription></Alert>}
    {!ready && controls}
    {error}
  </section>
}
