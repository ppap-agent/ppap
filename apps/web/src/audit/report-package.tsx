import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
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
  const itemLabels = {pending: '尚未确认', prepared: '打包准备完成', failed: '打包准备失败'}
  return <section aria-label="ZIP 工作" className="space-y-3 border-y py-4">
    <h2 className="font-semibold">{issue ? 'ZIP 状态待核对' : known ? labels[job.state] : 'ZIP 受理结果未知'}</h2>
    <p className="text-xs text-muted-foreground">共 {job.reports.length} 份报告</p>
    {job.reason && <p role="status">{job.reason}</p>}
    {issue && <Alert variant="destructive"><AlertTitle>ZIP 明细需要核对</AlertTitle><AlertDescription>{issue} 请读取原打包请求的状态。</AlertDescription></Alert>}
    <ItemGroup>{job.reports.map(reference => {
      const outcome = issue ? undefined : job.outcomes?.find(row => sameSnapshot(row.reference, reference))
      return <Item role="listitem" key={JSON.stringify(reference)} size="xs"><ItemContent><div className="flex flex-wrap items-center gap-2"><FileName name={outcome?.filename || `${nameFor(reference)}-审核报告`} format="pdf"/>{outcome ? <Badge variant={outcome.state === 'failed' ? 'destructive' : 'outline'}>{itemLabels[outcome.state]}</Badge> : <span className="text-xs text-muted-foreground">{issue ? '逐项状态待核对' : '未提供逐项状态'}</span>}</div>{outcome?.reason && <p className="text-sm">{outcome.reason}</p>}<p className="break-all text-xs text-muted-foreground">冻结文件：{reference.file_version_id} · 结果尝试：{reference.result_attempt}</p></ItemContent></Item>
    })}</ItemGroup>
    <div className="flex flex-wrap gap-2">{!issue && known && job.state === 'ready' ? <Button variant="outline" disabled={busy} onClick={onDownload}>下载已就绪 ZIP</Button> : <Button variant="outline" disabled={busy} onClick={onRead}>{busy ? '正在读取 ZIP 状态…' : '读取 ZIP 状态'}</Button>}{!issue && known && job.state === 'failed' && <Button variant="outline" disabled={busy || !job.retry_allowed} onClick={onRetry}>重试同一报告集合打包</Button>}</div>
    {downloadError && <Alert variant="destructive"><AlertTitle>ZIP 下载未完成</AlertTitle><AlertDescription>{downloadError}</AlertDescription></Alert>}
  </section>
}
