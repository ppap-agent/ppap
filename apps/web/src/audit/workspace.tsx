// Stitch: projects/13261964020862199279/screens/fae9ff65452e4d899ded37b452347e17
import {useContext, useEffect, useState} from 'react'
import {createPortal} from 'react-dom'
import {FileText, RefreshCw} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {readAuditRoute, taskAddress, writeAuditAddress} from './routes'
import {reviewed as hasReview, reviewable} from './review-navigation'
import {CompletionNotification, type AuditNotificationAdapter} from './completion-notification'
import {Item, ItemContent, ItemDescription, ItemGroup} from '../components/ui/item'
import {FileName} from '../journey/file-name'
import {reportReady, resultConsistencyError, taskProgressError, taskResultCounts} from './result-projection'
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

export function AuditWorkspace({task, busy, readError, onReadSupport, onRead, onChange, renderOriginal, reportAdapter, notificationAdapter, onReportChange, onReportView, sharingAdapter, onEditDisplay, headerActions, onReaudit}: {task: Task; busy: boolean; readError: string; onReadSupport: () => void; onRead: () => void; onChange: (task: Task) => void; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; notificationAdapter?: AuditNotificationAdapter; onReportChange: (task: Task) => void; onReportView: (open: boolean) => void; sharingAdapter?: AuditSharingAdapter; onEditDisplay?: (file: components['schemas']['AuditTaskFile']) => void; headerActions?: HTMLElement | null; onReaudit?: () => void}) {
  const openSupport = useContext(SupportEntryContext)
  const queue = task.files.filter(row => row.selection === 'selected')
  const progressIssue = taskProgressError(task)
  const targets = queue.filter(row => row.match?.state !== 'reference'), references = queue.filter(row => row.match?.state === 'reference')
  const counts = taskResultCounts(task), readyReports = targets.filter(row => reportReady(task, row)).length
  const [selected, setSelected] = useState(() => new URL(location.href).searchParams.get('file') || '')
  const [report, setReport] = useState<string | null>(() => {const id = new URL(location.href).searchParams.get('report'); return id === '1' ? queue[0]?.file.id || '' : id})
  useEffect(() => {
    const restore = () => {
      const url = new URL(location.href), route = readAuditRoute(url)
      if (route.surface !== 'audit' || route.taskId !== task.id) return
      const params = url.searchParams
      setSelected(params.get('file') || '')
      const id = params.get('report'); setReport(id === '1' ? task.files.find(row => row.selection === 'selected')?.file.id || '' : id)
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [task.id, task.files])
  function chooseFile(id: string) {
    setSelected(id)
    const url = new URL(location.href)
    if (id) url.searchParams.set('file', id); else url.searchParams.delete('file')
    url.searchParams.delete('clause'); url.searchParams.delete('focus')
    writeAuditAddress(url, 'push')
  }
  function showReport(id: string | null) {
    setReport(id); onReportView(id !== null)
    const url = new URL(location.href)
    if (id !== null) url.searchParams.set('report', id); else url.searchParams.delete('report')
    if (id === null) url.searchParams.delete('packageRequest')
    writeAuditAddress(url)
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: task.id, report: id, packageRequest: url.searchParams.get('packageRequest')}, location.origin)
  }
  const file = selected ? queue.find(row => row.file.id === selected) : undefined
  const overview = <section aria-label="全部文件概览" className="min-w-0 space-y-4 p-6 max-sm:p-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">全部文件</h2>{task.predecessor_id && <Button variant="link" asChild><a href={task.predecessor_task_id ? taskAddress(task.predecessor_task_id) : `/?round=${encodeURIComponent(task.predecessor_id)}`}>查看前次审核与人工记录</a></Button>}{task.successor_tasks?.map(next => <Button key={next.id} variant="link" asChild><a href={taskAddress(next.id)}>关联重审 {next.number}</a></Button>)}</div>
    <div className="rounded-lg border"><Table className="min-w-xl table-fixed"><TableHeader><TableRow><TableHead>文件与规则</TableHead><TableHead className="w-28">审核状态</TableHead><TableHead className="w-32">人工复核（选做）</TableHead><TableHead className="w-28 text-right">操作</TableHead></TableRow></TableHeader><TableBody>{queue.map(row => {
      const invalid = resultConsistencyError(task, row), reference = row.match?.state === 'reference'
      const checks = (row.audit?.checks || []).filter(reviewable), reviewed = checks.filter(hasReview).length
      return <TableRow key={row.file.id}><TableCell className="whitespace-normal"><FileName colored name={row.file.original_filename}/><p className="mt-2 text-xs text-muted-foreground">{reference ? '比对资料 · 已固定版本' : row.audit?.rule.title || row.match?.rule?.title}</p></TableCell><TableCell><Badge variant={!invalid && row.audit?.conclusion === 'NG' ? 'destructive' : 'outline'}>{invalid ? '结果待核对' : reference ? '比对资料' : row.audit?.conclusion || (row.audit ? executionLabel[row.audit.execution] : '等待读取')}</Badge>{row.audit && <p className="mt-2 text-xs text-muted-foreground">{row.audit.progress.completed}/{row.audit.progress.total} 项<br/>{reportLabel[row.audit.report.state]}</p>}</TableCell><TableCell><span className="text-xs tabular-nums text-muted-foreground">{reference ? '—' : `${reviewed}/${checks.length}`}</span></TableCell><TableCell className="text-right"><Button variant="ghost" size="sm" className="whitespace-nowrap" onClick={() => chooseFile(row.file.id)}>{reference ? '查看资料' : '查看结果'}</Button></TableCell></TableRow>
    })}</TableBody></Table></div>
  </section>
  const fileQueue = <div><Button variant={selected ? "ghost" : "secondary"} className="w-full justify-start" onClick={() => chooseFile("")}>全部文件概览</Button><nav aria-label="文件队列">{[{title: '审核文件', rows: targets}, {title: '比对资料', rows: references}].filter(group => group.rows.length).map(group => <section key={group.title} className="min-w-0"><h2 className="border-b bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">{group.title} ({group.rows.length})</h2><div>{group.rows.map(row => {
    const invalid = resultConsistencyError(task, row)
    const state = row.match?.state === 'reference' ? '比对资料 · 已固定版本' : invalid ? '结果待核对' : row.audit ? row.audit.execution === 'completed' ? row.audit.conclusion ?? '结果未知' : executionLabel[row.audit.execution] : '等待读取结果'
    return <div key={row.file.id} className={file?.file.id === row.file.id ? 'border-b border-l-2 border-l-foreground' : 'border-b border-l-2 border-l-transparent'}><Button size="row" aria-pressed={file?.file.id === row.file.id} aria-label={`${row.file.original_filename} ${state}${row.audit && !invalid ? ` ${row.audit.progress.completed}/${row.audit.progress.total} 项 · ${reportLabel[row.audit.report.state]}` : ''}`} variant="ghost" className="min-w-0" onClick={() => chooseFile(row.file.id)}><span className="grid min-w-0 gap-1"><FileName colored name={row.file.original_filename}/><span className={!invalid && row.audit?.conclusion === 'NG' ? 'pl-8 text-xs text-destructive' : 'pl-8 text-xs text-muted-foreground'}>{state}{row.audit && !invalid && <>{row.audit.execution === 'completed' ? ` · 符合 ${row.audit.counts.pass_count} / 不符合 ${row.audit.counts.ng_count}` : ` · ${row.audit.progress.completed}/${row.audit.progress.total} 项`}{row.audit.counts.unknown_count > 0 && ` · 无法判断 ${row.audit.counts.unknown_count}`}{row.audit.counts.not_covered_count > 0 && ` · 未覆盖 ${row.audit.counts.not_covered_count}`}{row.audit.counts.error_count > 0 && ` · 技术错误 ${row.audit.counts.error_count}`}</>}</span></span></Button></div>
  })}</div></section>)}</nav><AuditExcludedFiles files={task.files}/></div>
  const actions = <div hidden={report !== null}><div className="flex shrink-0 items-center gap-2">{onReaudit && task.state === "completed" && <Button variant="outline" onClick={onReaudit}>发起重审</Button>}<Button aria-label="报告中心" onClick={() => showReport(file?.match?.state === 'reference' ? '' : file?.file.id || '')}><FileText/>报告中心{readyReports > 0 && <span>({readyReports})</span>}</Button><Button variant="outline" size="icon" aria-label="读取审核结果" title="刷新审核结果" disabled={busy} onClick={onRead}><RefreshCw className={busy ? 'animate-spin' : ''}/></Button>{task.notification_enabled !== undefined && notificationAdapter && <CompletionNotification compact task={task} adapter={notificationAdapter} onChange={preference => onChange({...task, notification_enabled: preference.enabled, notification_revision: preference.revision})}/>}</div></div>
  return <section>
    {headerActions && createPortal(actions, headerActions)}
    {report !== null && <AuditReports task={task} initialFile={report} onChoose={showReport} onBack={() => showReport(null)} onRead={onRead} busy={busy} readError={readError} onReadSupport={onReadSupport} adapter={reportAdapter} onChange={onReportChange} sharingAdapter={sharingAdapter}/>}
    <div hidden={report !== null}><header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-b px-6 py-3 max-sm:px-4"><div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2"><h1 className="text-2xl font-semibold">审核结果</h1><Badge shape="square" variant="outline">{progressIssue ? '进度待核对' : task.state === 'completed' ? '审核完成' : '审核中'}</Badge><p role="status" className="text-xs text-muted-foreground">{progressIssue ? '任务进度待核对' : <>{task.state === 'completed' ? '审核已结束' : '审核中'} · 文件已结束 {task.progress?.completed ?? '—'}/{task.progress?.total ?? targets.length}</>}</p>{references.length > 0 && <span className="text-xs text-muted-foreground">比对资料 {references.length} 份</span>}{counts && <><span className="text-xs text-muted-foreground">共 {counts.items} 项</span><div className="flex flex-wrap gap-1.5"><Badge shape="square" variant="success">符合 {counts.pass_count}</Badge><Badge shape="square" variant={counts.ng_count ? 'destructive' : 'outline'}>不符合 {counts.ng_count}</Badge>{counts.unknown_count > 0 && <Badge shape="square" variant="outline">无法判断 {counts.unknown_count}</Badge>}{counts.not_covered_count > 0 && <Badge shape="square" variant="outline">未覆盖 {counts.not_covered_count}</Badge>}{counts.error_count > 0 && <Badge shape="square" variant="outline">技术错误 {counts.error_count}</Badge>}</div></>}</div>{!headerActions && actions}</header>
      <div className="space-y-3 empty:hidden [&:not(:empty)]:border-b [&:not(:empty)]:px-6 [&:not(:empty)]:py-3">
      <AuditTaskEstimate task={task} progressIssue={Boolean(progressIssue)}/>
      {progressIssue && <Alert variant="destructive"><AlertTitle>任务进度需要核对</AlertTitle><AlertDescription>{progressIssue} 各文件的有效依据仍保留，请重新读取审核结果。</AlertDescription></Alert>}
      {task.usage_notice && <Alert><AlertTitle>用量提示</AlertTitle><AlertDescription>{task.usage_notice}{openSupport && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '审核用量提示', facts: [{label: '任务标识', value: task.id}, {label: '任务编号', value: task.number}, ...(task.round_id ? [{label: '审核轮次', value: task.round_id}] : []), {label: '服务提示', value: task.usage_notice!}]})}>联系支持</Button>}</AlertDescription></Alert>}{readError && <Alert variant="destructive"><AlertTitle>结果暂时无法读取</AlertTitle><AlertDescription><p>{readError} 原有结果与选择仍保留。</p><Button variant="outline" size="sm" onClick={onReadSupport}>联系支持</Button></AlertDescription></Alert>}
      </div>
      <div className="grid min-w-0 min-[900px]:grid-cols-[15rem_minmax(0,1fr)] xl:grid-cols-[16.25rem_minmax(0,1fr)]"><aside className="min-w-0 border-b min-[900px]:min-h-[calc(100dvh-8.5rem)] min-[900px]:border-r min-[900px]:border-b-0">{fileQueue}</aside><div className="min-w-0">{file && task.round_id ? file.match?.state === 'reference' ? <section className="min-w-0 space-y-4 p-6 max-sm:p-4" aria-label="比对资料原件"><h2 className="font-semibold"><FileName name={file.file.original_filename}/></h2><p className="text-sm text-muted-foreground">本轮比对资料，已固定版本。不单独审核、不计费，也不生成单独报告。</p>{renderOriginal?.(file.file, null, task.round_id)}</section> : <AuditReviewContent key={`${task.round_id}:${file.file.id}`} file={file} roundId={task.round_id} frozenFileVersionIds={queue.map(row => row.file.id)} originals={queue.map(row => ({file_version_id: row.file.id, file: row.file, access: {state: 'allowed', reason: null}}))} renderOriginal={renderOriginal} onReport={() => showReport(file.file.id)} onEditDisplay={onEditDisplay ? () => onEditDisplay(file) : undefined}/> : overview}</div></div>
    </div>
  </section>
}
