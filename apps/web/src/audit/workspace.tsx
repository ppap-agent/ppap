// Stitch: projects/13261964020862199279/screens/d87bdaa1ba7c4dbf9178ed1bc7a6bde9
import {useContext, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {CompletionNotification, type AuditNotificationAdapter} from './completion-notification'
import {Item, ItemContent, ItemDescription, ItemGroup} from '../components/ui/item'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {FileName} from '../journey/file-name'
import {resultConsistencyError, taskProgressError} from './result-projection'
import {AuditReports} from './reports'
import {AuditReviewContent, type OriginalRenderer} from './review-content'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {AuditTaskEstimate} from './task-estimate'
import {SupportEntryContext} from './support'

type Task = components['schemas']['AuditTask']
const executionLabel = {queued: '排队中', running: '审核中', completed: '审核结束', review_failed: '异常'}
const reportLabel = {not_eligible: '无正式报告', pending: '报告待生成', generating: '报告生成中', ready: '报告已就绪', failed: '报告生成失败'}

export function AuditExcludedFiles({files}: {files: Task['files']}) {
  return <div className="space-y-5">{(['unselected', 'removed'] as const).map(selection => {
    const rows = files.filter(row => row.selection === selection)
    const title = selection === 'unselected' ? '未选择文件' : '已移除文件'
    return rows.length > 0 && <section key={selection} aria-label={title} className="space-y-2 border-t pt-4"><h2 className="text-sm font-semibold">{title}</h2><ItemGroup>{rows.map(row => <Item role="listitem" key={row.file.id} size="xs"><ItemContent className="min-w-0"><FileName name={row.file.original_filename}/><ItemDescription wrap>{selection === 'unselected' ? '本次未选择，不进入审核' : row.removal_reason || '移除原因未提供'}</ItemDescription></ItemContent></Item>)}</ItemGroup></section>
  })}</div>
}

export function AuditWorkspace({task, busy, readError, onReadSupport, onRead, onChange, renderOriginal, reportAdapter, notificationAdapter, onReportChange, onReportView, sharingAdapter, onEditDisplay}: {task: Task; busy: boolean; readError: string; onReadSupport: () => void; onRead: () => void; onChange: (task: Task) => void; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; notificationAdapter?: AuditNotificationAdapter; onReportChange: (task: Task) => void; onReportView: (open: boolean) => void; sharingAdapter?: AuditSharingAdapter; onEditDisplay?: (file: components['schemas']['AuditTaskFile']) => void}) {
  const openSupport = useContext(SupportEntryContext)
  const queue = task.files.filter(row => row.selection === 'selected')
  const progressIssue = taskProgressError(task)
  const [selected, setSelected] = useState(queue[0]?.file.id || ''), [queueOpen, setQueueOpen] = useState(false)
  const [report, setReport] = useState<string | null>(() => {const id = new URL(location.href).searchParams.get('report'); return id === '1' ? queue[0]?.file.id || '' : id})
  function showReport(id: string | null) {
    setReport(id); onReportView(id !== null)
    const url = new URL(location.href)
    if (id !== null) url.searchParams.set('report', id); else url.searchParams.delete('report')
    if (id === null) url.searchParams.delete('packageRequest')
    history.replaceState(null, '', url)
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: task.id, report: id, packageRequest: url.searchParams.get('packageRequest')}, location.origin)
  }
  const file = queue.find(row => row.file.id === selected) ?? queue[0]
  const fileQueue = <div className="space-y-5"><nav aria-label="文件队列" className="grid gap-2">{queue.map(row => {
    const invalid = resultConsistencyError(task, row)
    return <Button key={row.file.id} aria-pressed={file?.file.id === row.file.id} variant={file?.file.id === row.file.id ? 'secondary' : 'ghost'} className="h-auto min-w-0 justify-start whitespace-normal text-left" onClick={() => {setSelected(row.file.id); setQueueOpen(false)}}><span className="grid min-w-0 gap-2"><FileName name={row.file.original_filename}/><span className="text-xs">{row.match?.state === 'reference' ? '比对资料 · 已固定版本' : invalid ? '结果待核对' : row.audit ? row.audit.execution === 'completed' ? row.audit.conclusion ?? '结果未知' : executionLabel[row.audit.execution] : '等待读取结果'}</span>{row.audit && !invalid && <span className="text-xs">{row.audit.progress.completed}/{row.audit.progress.total} 项 · {reportLabel[row.audit.report.state]}</span>}</span></Button>
  })}</nav><AuditExcludedFiles files={task.files}/></div>
  return <section>
    {report !== null && <AuditReports task={task} initialFile={report} onChoose={showReport} onBack={() => showReport(null)} onRead={onRead} busy={busy} readError={readError} onReadSupport={onReadSupport} adapter={reportAdapter} onChange={onReportChange} sharingAdapter={sharingAdapter}/>}
    <div hidden={report !== null} className="space-y-5"><header className="flex flex-wrap items-center justify-between gap-3"><div className="space-y-2"><h1 className="text-2xl font-semibold">审核工作台</h1><p role="status" className="text-muted-foreground">{progressIssue ? '任务进度待核对' : <>{task.state === 'completed' ? '审核已结束' : '审核中'} · 文件已结束 {task.progress?.completed ?? '—'}/{task.progress?.total ?? queue.length}</>}</p></div><div className="flex flex-wrap items-center gap-3"><Button variant="outline" onClick={() => showReport(file?.file.id || '')}>报告中心</Button>{task.notification_enabled !== undefined && notificationAdapter && <CompletionNotification task={task} adapter={notificationAdapter} onChange={preference => onChange({...task, notification_enabled: preference.enabled, notification_revision: preference.revision})}/>}<Button variant="outline" disabled={busy} onClick={onRead}>{busy ? '正在读取审核结果…' : '读取审核结果'}</Button></div></header>
      <AuditTaskEstimate task={task} progressIssue={Boolean(progressIssue)}/>
      {progressIssue && <Alert variant="destructive"><AlertTitle>任务进度需要核对</AlertTitle><AlertDescription>{progressIssue} 各文件的有效依据仍保留，请重新读取审核结果。</AlertDescription></Alert>}
      {task.usage_notice && <Alert><AlertTitle>用量提示</AlertTitle><AlertDescription>{task.usage_notice}{openSupport && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '审核用量提示', facts: [{label: '任务标识', value: task.id}, {label: '任务编号', value: task.number}, ...(task.round_id ? [{label: '审核轮次', value: task.round_id}] : []), {label: '服务提示', value: task.usage_notice!}]})}>联系支持</Button>}</AlertDescription></Alert>}{readError && <Alert variant="destructive"><AlertTitle>结果暂时无法读取</AlertTitle><AlertDescription><p>{readError} 原有结果与选择仍保留。</p><Button variant="outline" size="sm" onClick={onReadSupport}>联系支持</Button></AlertDescription></Alert>}
      <Sheet open={queueOpen} onOpenChange={setQueueOpen}><SheetTrigger asChild><Button className="md:hidden" variant="outline">文件队列 · {queue.length} 份</Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>文件队列</SheetTitle><SheetDescription className="sr-only">查看进入审核、未选择与已移除的文件</SheetDescription></SheetHeader><div className="overflow-y-auto p-4">{fileQueue}</div></SheetContent></Sheet>
      <div className="grid min-w-0 gap-6 md:grid-cols-[12rem_minmax(0,1fr)]"><aside className="min-w-0 border-r pr-4 max-md:hidden">{fileQueue}</aside>{file && task.round_id ? file.match?.state === 'reference' ? <section className="min-w-0 space-y-4" aria-label="比对资料原件"><h2 className="font-semibold"><FileName name={file.file.original_filename}/></h2><p className="text-sm text-muted-foreground">本轮比对资料，已固定版本。不单独审核、不计费，也不生成单独报告。</p>{renderOriginal?.(file.file, null, task.round_id)}</section> : <AuditReviewContent key={`${task.round_id}:${file.file.id}`} file={file} roundId={task.round_id} frozenFileVersionIds={queue.map(row => row.file.id)} originals={queue.map(row => ({file_version_id: row.file.id, file: row.file, access: {state: 'allowed', reason: null}}))} renderOriginal={renderOriginal} onReport={() => showReport(file.file.id)} onEditDisplay={onEditDisplay ? () => onEditDisplay(file) : undefined}/> : <p>没有进入审核的文件。请返回任务列表。</p>}</div>
    </div>
  </section>
}
