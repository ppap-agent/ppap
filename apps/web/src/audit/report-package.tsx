import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {FileName} from '../journey/file-name'

type Task = components['schemas']['AuditTask']
export function ReportPackage({task, busy, onRead, onDownload, onRetry, downloadError}: {task: Task; busy: boolean; onRead: () => void; onDownload: () => void; onRetry: () => void; downloadError: string}) {
  const job = task.report_package
  if (!job) return null
  const known = job.receipt.state !== 'pending'
  const labels = {pending: '等待打包', generating: '打包中', ready: 'ZIP 已就绪', failed: 'ZIP 打包失败'}
  return <section aria-label="ZIP 工作" className="space-y-3 border-y py-4">
    <h2 className="font-semibold">{known ? labels[job.state] : 'ZIP 受理结果未知'}</h2>
    <p className="text-xs text-muted-foreground">固定 {job.reports.length} 份报告。改变勾选不会修改此包；单份下载不受影响。</p>
    {job.reason && <p role="status">{job.reason}</p>}
    <ItemGroup>{job.reports.map(reference => <Item role="listitem" key={`${reference.file_version_id}-${reference.report_id}`} size="xs"><ItemContent><FileName name={`${task.files.find(file => file.file.id === reference.file_version_id)?.file.original_filename || '历史文件'}-审核报告`}/><p className="break-all text-xs text-muted-foreground">冻结文件：{reference.file_version_id} · 结果尝试：{reference.result_attempt}</p></ItemContent></Item>)}</ItemGroup>
    <div className="flex flex-wrap gap-2">{known && job.state === 'ready' ? <Button variant="outline" disabled={busy} onClick={onDownload}>下载已就绪 ZIP</Button> : <Button variant="outline" disabled={busy} onClick={onRead}>{busy ? '正在读取 ZIP 状态…' : '读取 ZIP 状态'}</Button>}{known && job.state === 'failed' && <Button variant="outline" disabled={busy || !job.retry_allowed} onClick={onRetry}>重试同一报告集合打包</Button>}</div>
    {downloadError && <Alert variant="destructive"><AlertTitle>ZIP 下载未完成</AlertTitle><AlertDescription>{downloadError}</AlertDescription></Alert>}
  </section>
}
