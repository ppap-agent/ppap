// Stitch: projects/13261964020862199279/screens/fae9ff65452e4d899ded37b452347e17
import {useContext, useEffect, useState} from 'react'
import {createPortal} from 'react-dom'
import {ArrowRight, ChevronDown, FileText, RefreshCw} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {auditAddressChanged, readAuditRoute, taskAddress, writeAuditAddress} from './routes'
import {filterChecks, reviewed as hasReview, reviewable} from './review-navigation'
import {Progress} from '../components/ui/progress'
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
import {hasExecutionError, summarizeTask} from './task-summary'
import {DeepFeedback} from './deep-feedback'
import {FactQuestions} from './fact-questions'
import {factMachineFindings} from './fact-answers-contract'
import type {ReadRound} from './round-reader'
import {conclusionLabel, executionLabel, reportLabel, resultLabel, resultTone, uncoveredLabel} from './status-labels'

type Task = components['schemas']['AuditTask']
type Round = components['schemas']['Round']

export function AuditExcludedFiles({files}: {files: Task['files']}) {
  return <div className="space-y-5">{(['unselected', 'removed'] as const).map(selection => {
    const rows = files.filter(row => row.selection === selection)
    const title = selection === 'unselected' ? '未选择文件' : '已移除文件'
    return rows.length > 0 && <section key={selection} aria-label={title} className="space-y-2 border-t pt-4"><h2 className="text-sm font-semibold">{title}</h2><ItemGroup>{rows.map(row => <Item role="listitem" key={row.file.id} size="xs"><ItemContent className="min-w-0"><FileName name={row.file.original_filename}/><ItemDescription wrap>{selection === 'unselected' ? '本次未选择，不进入审核' : row.removal_reason || '移除原因未提供'}</ItemDescription></ItemContent></Item>)}</ItemGroup></section>
  })}</div>
}

export function AuditWorkspace({task, busy, readError, onReadSupport, onRead, onChange, renderOriginal, reportAdapter, notificationAdapter, onReportChange, onReportView, sharingAdapter, onEditDisplay, headerActions, onReaudit, readRound}: {task: Task; busy: boolean; readError: string; onReadSupport: () => void; onRead: () => void; onChange: (task: Task) => void; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; notificationAdapter?: AuditNotificationAdapter; onReportChange: (task: Task) => void; onReportView: (open: boolean) => void; sharingAdapter?: AuditSharingAdapter; onEditDisplay?: (file: components['schemas']['AuditTaskFile']) => void; headerActions?: HTMLElement | null; onReaudit?: () => void; readRound?: ReadRound}) {
  const openSupport = useContext(SupportEntryContext)
  const queue = task.files.filter(row => row.selection === 'selected')
  const progressIssue = taskProgressError(task)
  const executionFailed = hasExecutionError(summarizeTask(task))
  const targets = queue.filter(row => row.match?.state !== 'reference'), references = queue.filter(row => row.match?.state === 'reference')
  const counts = taskResultCounts(task), readyReports = targets.filter(row => reportReady(task, row)).length
  const [selected, setSelected] = useState(() => new URL(location.href).searchParams.get('file') || '')
  const [feedbackChanged, setFeedbackChanged] = useState(false)
  const [queueOpen, setQueueOpen] = useState(false)
  const [report, setReport] = useState<string | null>(() => new URL(location.href).searchParams.get('report'))
  const [previousFiles, setPreviousFiles] = useState<NonNullable<Round['read_view']>['files']>([])
  // A re-audit is read against the round it follows; without that read the page just omits the comparison.
  useEffect(() => {
    if (!task.predecessor_id || !readRound) return
    const controller = new AbortController()
    void readRound(task.predecessor_id, controller.signal).then(round => {if (!controller.signal.aborted) setPreviousFiles(round.read_view?.files ?? [])}).catch(() => {})
    return () => controller.abort()
  }, [task.predecessor_id, readRound])
  useEffect(() => {
    const restore = () => {
      const url = new URL(location.href), route = readAuditRoute(url)
      if (route.surface !== 'audit' || route.taskId !== task.id) return
      const params = url.searchParams
      setSelected(params.get('file') || '')
      setReport(params.get('report'))
    }
    window.addEventListener('popstate', restore)
    window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [task.id, task.files])
  function chooseFile(id: string) {
    setQueueOpen(false)
    setSelected(id)
    const url = new URL(location.href)
    if (id) url.searchParams.set('file', id); else url.searchParams.delete('file')
    url.searchParams.delete('clause'); url.searchParams.delete('focus'); url.searchParams.delete('basis')
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
  const previousFile = file && previousFiles.find(row => row.file.id === file.file.id || row.file.id === file.replacement_of_file_version_id || row.file.file_id === file.file.file_id)
  // The overview leads with what is still running and which finished items need a person.
  const itemsDone = targets.reduce((sum, row) => sum + (row.audit?.progress.completed ?? 0), 0)
  const itemsTotal = targets.reduce((sum, row) => sum + (row.audit?.progress.total ?? 0), 0)
  const severity = (check: NonNullable<Task['files'][number]['audit']>['checks'][number]) => check.execution === 'failed' ? 1 : check.coverage !== 'executed' ? 3 : check.finding?.status === 'NG' ? 0 : check.finding?.status === 'ERR' ? 1 : 2
  const issues = targets.flatMap(row => resultConsistencyError(task, row) ? [] : filterChecks(row.audit?.checks || [], '', 'attention').filter(check => ['completed', 'failed'].includes(check.execution)).map(check => ({row, check}))).sort((a, b) => severity(a.check) - severity(b.check))
  const issueLabel = (check: (typeof issues)[number]['check']) => check.execution === 'failed' ? resultLabel.ERR : check.coverage !== 'executed' ? uncoveredLabel : check.finding ? resultLabel[check.finding.status] : '结果未知'
  function openIssue(fileId: string, clauseId: string) {
    setSelected(fileId)
    const url = new URL(location.href)
    url.searchParams.set('file', fileId); url.searchParams.set('clause', clauseId); url.searchParams.delete('focus'); url.searchParams.set('checkFilter', 'attention'); url.searchParams.set('basis', '1')
    writeAuditAddress(url, 'push')
  }
  const running = task.state !== 'completed' && !progressIssue && itemsTotal > 0
  const overview = <section aria-label="全部文件概览" className="min-w-0 space-y-4 p-6 max-sm:p-4">
    {running && <section aria-label="审核进度" className="space-y-2 rounded-lg border p-4"><div className="flex flex-wrap items-baseline justify-between gap-2"><p className="font-medium">正在审核</p><p className="text-sm tabular-nums text-muted-foreground">审核项 {itemsDone}/{itemsTotal} 已结束</p></div><Progress aria-label="审核项完成进度" value={Math.min(100, itemsDone / itemsTotal * 100)}/><p className="text-xs text-muted-foreground">完成后各文件的结果会自动出现在这里，无需刷新。</p></section>}
    {issues.length > 0 && <section aria-label="需关注的审核项" className="space-y-2 rounded-lg border p-4"><div className="flex flex-wrap items-baseline justify-between gap-2"><h2 className="font-medium">需关注 · {issues.length} 项</h2>{issues.length > 5 && <span className="text-xs text-muted-foreground">按严重程度显示前 5 项</span>}</div><ul className="divide-y">{issues.slice(0, 5).map(({row, check}) => <li key={`${row.file.id}:${check.rule_item.id}`}><Button variant="ghost" size="sm" className="w-full min-w-0 justify-start" onClick={() => openIssue(row.file.id, check.rule_item.id)}><span className={`size-2 shrink-0 rounded-full ${check.finding?.status === 'NG' && check.coverage === 'executed' && check.execution === 'completed' ? 'bg-destructive' : 'bg-warning'}`} aria-hidden="true"/><span className="min-w-0 flex-1 truncate text-left text-sm">{`${issueLabel(check)} · ${row.file.original_filename} · ${check.rule_item.id} ${check.rule_item.category}`}</span><ArrowRight aria-hidden="true" className="text-muted-foreground"/></Button></li>)}</ul></section>}
    {!running && issues.length === 0 && <p className="text-sm text-muted-foreground">{task.state === 'completed' && !executionFailed && !progressIssue ? '没有需关注的审核项。从文件列表查看逐项结果和依据。' : '从文件列表查看各文件的状态。'}</p>}
    {(task.predecessor_id || task.successor_tasks?.length) && <div className="flex flex-wrap items-center gap-3">{task.predecessor_id && <Button variant="link" asChild><a href={task.predecessor_task_id ? taskAddress(task.predecessor_task_id) : `/?round=${encodeURIComponent(task.predecessor_id)}`}>查看前次审核与人工记录</a></Button>}{task.successor_tasks?.map(next => <Button key={next.id} variant="link" asChild><a href={taskAddress(next.id)}>关联重审 {next.number}</a></Button>)}</div>}
  </section>
  const fileQueue = <div><Button variant={selected ? "ghost" : "secondary"} className="w-full justify-start" onClick={() => chooseFile("")}>全部文件概览</Button><nav aria-label="文件队列">{[{title: '审核文件', rows: targets}, {title: '比对资料', rows: references}].filter(group => group.rows.length).map(group => <section key={group.title} className="min-w-0"><h2 className="border-b bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">{group.title} ({group.rows.length})</h2><div>{group.rows.map(row => {
    const invalid = resultConsistencyError(task, row)
    const state = row.match?.state === 'reference' ? '比对资料 · 已固定版本' : invalid ? '结果待核对' : row.audit ? row.audit.execution === 'completed' ? row.audit.conclusion ? conclusionLabel[row.audit.conclusion] ?? '结果未知' : '结果未知' : executionLabel[row.audit.execution] : '等待读取结果'
    return <div key={row.file.id} className={file?.file.id === row.file.id ? 'border-b border-l-2 border-l-foreground' : 'border-b border-l-2 border-l-transparent'}><Button size="row" aria-pressed={file?.file.id === row.file.id} aria-label={`${row.file.original_filename} ${state}${row.audit && !invalid ? ` ${row.audit.progress.completed}/${row.audit.progress.total} 项 · ${reportLabel[row.audit.report.state]}` : ''}`} variant="ghost" className="min-w-0" onClick={() => chooseFile(row.file.id)}><span className="grid min-w-0 gap-1"><FileName colored truncate name={row.file.original_filename}/><span className={!invalid && row.audit?.conclusion === 'NG' ? 'pl-8 text-xs text-destructive' : 'pl-8 text-xs text-muted-foreground'}>{row.audit && !invalid && row.audit.execution === 'completed' && row.audit.counts.ng_count > 0 ? `${resultLabel.NG} ${row.audit.counts.ng_count}` : state}{row.audit && !invalid && <>{row.audit.execution === 'completed' ? '' : ` · ${row.audit.progress.completed}/${row.audit.progress.total} 项`}{row.audit.counts.unknown_count > 0 && ` · ${resultLabel.UNABLE} ${row.audit.counts.unknown_count}`}{row.audit.counts.not_covered_count > 0 && ` · ${uncoveredLabel} ${row.audit.counts.not_covered_count}`}{row.audit.counts.error_count > 0 && ` · ${resultLabel.ERR} ${row.audit.counts.error_count}`}</>}</span>{row.audit && !invalid && row.audit.checks.some(reviewable) && <span className="pl-8 text-xs tabular-nums text-muted-foreground">人工复核 {row.audit.checks.filter(hasReview).length}/{row.audit.checks.filter(reviewable).length}</span>}</span></Button></div>
  })}</div></section>)}</nav><AuditExcludedFiles files={task.files}/></div>
  const actions = <div hidden={report !== null}><div className="flex shrink-0 items-center gap-2">{onReaudit && task.state === "completed" && <Button variant="outline" onClick={onReaudit}>发起重审</Button>}<Button variant={task.analysis_mode === 'deep' ? 'outline' : 'default'} aria-label="报告中心" onClick={() => showReport(file?.match?.state === 'reference' ? '' : file?.file.id || '')}><FileText/>报告中心{readyReports > 0 && <span>({readyReports})</span>}</Button><Button variant="outline" size="icon" aria-label="读取审核结果" title="刷新审核结果" disabled={busy} onClick={onRead}><RefreshCw className={busy ? 'animate-spin' : ''}/></Button>{task.notification_enabled !== undefined && notificationAdapter && <CompletionNotification compact task={task} adapter={notificationAdapter} onChange={preference => onChange({...task, notification_enabled: preference.enabled, notification_revision: preference.revision})}/>}</div></div>
  return <section>
    {headerActions && createPortal(actions, headerActions)}
    {report !== null && <AuditReports headerActions={headerActions} task={task} initialFile={report === '1' ? '' : report} onChoose={showReport} onRead={onRead} busy={busy} readError={readError} onReadSupport={onReadSupport} adapter={reportAdapter} onChange={onReportChange} sharingAdapter={sharingAdapter}/>}
    <div hidden={report !== null}><header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-6 py-2 max-sm:px-4"><div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"><h1 className="text-lg font-semibold">{feedbackChanged ? '此前审核结果' : '审核结果'}</h1><Badge shape="square" variant={progressIssue ? 'warning' : task.state === 'completed' && executionFailed ? 'destructive' : 'outline'}>{progressIssue ? '进度待核对' : task.state === 'completed' ? executionFailed ? '审核异常' : '审核完成' : '审核中'}</Badge><p role="status" className={task.state === 'completed' && !progressIssue ? 'sr-only' : 'text-xs text-muted-foreground'}>{progressIssue ? '任务进度待核对' : <>文件已结束 {task.progress?.completed ?? '—'}/{task.progress?.total ?? targets.length}</>}</p>{references.length > 0 && <span className="text-xs text-muted-foreground">比对资料 {references.length} 份</span>}{counts && !file && <><span className="text-xs text-muted-foreground">共 {counts.items} 项</span><div className="flex flex-wrap gap-1.5"><Badge shape="square" variant={counts.ng_count ? 'destructive' : 'outline'}>{resultLabel.NG} {counts.ng_count}</Badge>{counts.error_count > 0 && <Badge shape="square" variant="warning">{resultLabel.ERR} {counts.error_count}</Badge>}{counts.unknown_count > 0 && <Badge shape="square" variant="warning">{resultLabel.UNABLE} {counts.unknown_count}</Badge>}{counts.not_covered_count > 0 && <Badge shape="square" variant="warning">{uncoveredLabel} {counts.not_covered_count}</Badge>}<Badge shape="square" variant="success">{resultLabel.PASS} {counts.pass_count}</Badge></div></>}</div>{!headerActions && actions}</header>
      {task.state === 'completed' && task.round_id && <FactQuestions key={`facts:${task.round_id}`} roundId={task.round_id} files={queue.map(row => row.file)} findings={queue.flatMap(row => factMachineFindings(row.audit?.checks))}/>}
      {task.analysis_mode === 'deep' && task.round_id && <DeepFeedback key={task.round_id} roundId={task.round_id} files={queue.map(row => row.file)} rules={queue.flatMap(row => {const rule = row.audit?.rule || row.match?.rule; return rule?.body.kind === 'engine' ? rule.body.rules.map(item => ({documentId: row.file.id, filename: row.file.original_filename, rule: item})) : []})} renderOriginal={renderOriginal} onBasisChanged={setFeedbackChanged}/>}
      <div className="space-y-3 empty:hidden [&:not(:empty)]:border-b [&:not(:empty)]:px-6 [&:not(:empty)]:py-3">
      <AuditTaskEstimate task={task} progressIssue={Boolean(progressIssue)}/>
      {progressIssue && <Alert variant="destructive"><AlertTitle>任务进度需要核对</AlertTitle><AlertDescription>{progressIssue} 各文件的有效依据仍保留，请重新读取审核结果。</AlertDescription></Alert>}
      {task.usage_notice && <Alert><AlertTitle>用量提示</AlertTitle><AlertDescription>{task.usage_notice}{openSupport && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '审核用量提示', facts: [{label: '任务标识', value: task.id}, {label: '任务编号', value: task.number}, ...(task.round_id ? [{label: '审核轮次', value: task.round_id}] : []), {label: '服务提示', value: task.usage_notice!}]})}>联系支持</Button>}</AlertDescription></Alert>}{readError && <Alert variant="destructive"><AlertTitle>结果暂时无法读取</AlertTitle><AlertDescription><p>{readError} 原有结果与选择仍保留。</p><Button variant="outline" size="sm" onClick={onReadSupport}>联系支持</Button></AlertDescription></Alert>}
      </div>
      <div className="grid min-w-0 min-[900px]:grid-cols-[15rem_minmax(0,1fr)] xl:grid-cols-[16.25rem_minmax(0,1fr)]"><aside className="min-w-0 border-b min-[900px]:min-h-[calc(100dvh-8.5rem)] min-[900px]:border-r min-[900px]:border-b-0">{file && <Button variant="ghost" className="w-full justify-between min-[900px]:hidden" aria-expanded={queueOpen} onClick={() => setQueueOpen(value => !value)}><span className="min-w-0 truncate">切换文件 · {file.file.original_filename}</span><ChevronDown aria-hidden="true" className={queueOpen ? 'rotate-180 transition-transform' : 'transition-transform'}/></Button>}<div className={file && !queueOpen ? 'max-[899px]:hidden' : undefined}>{fileQueue}</div></aside><div className="min-w-0">{file && task.round_id ? file.match?.state === 'reference' ? <section className="min-w-0 space-y-4 p-6 max-sm:p-4" aria-label="比对资料原件"><h2 className="font-semibold"><FileName name={file.file.original_filename}/></h2><p className="text-sm text-muted-foreground">本轮比对资料，已固定版本。不单独审核、不计费，也不生成单独报告。</p>{renderOriginal?.(file.file, null, task.round_id)}</section> : <AuditReviewContent onReaudit={onReaudit} previous={previousFile?.audit ? {label: '上次审核', checks: previousFile.audit.checks} : null} key={`${task.round_id}:${file.file.id}`} file={file} roundId={task.round_id} taskId={task.id} frozenFileVersionIds={queue.map(row => row.file.id)} originals={queue.map(row => ({file_version_id: row.file.id, file: row.file, access: {state: 'allowed', reason: null}}))} renderOriginal={renderOriginal} onReport={() => showReport(file.file.id)} onEditDisplay={onEditDisplay ? () => onEditDisplay(file) : undefined}/> : overview}</div></div>
    </div>
  </section>
}
