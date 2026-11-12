// Stitch: projects/13261964020862199279/screens/fae9ff65452e4d899ded37b452347e17
import {useContext, useEffect, useState, type ReactNode} from 'react'
import {createPortal} from 'react-dom'
import {ArrowRight, FileText, RefreshCw} from 'lucide-react'
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
import {clauseLabel, reportReady, resultConsistencyError, taskProgressError, taskResultCounts} from './result-projection'
import {AuditReports} from './reports'
import {AuditReviewContent, type OriginalRenderer} from './review-content'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {AuditTaskEstimate} from './task-estimate'
import {SupportEntryContext} from './support'
import {hasExecutionError, subjectLabel, summarizeTask, taskSubject} from './task-summary'
import {fileDisplayValues} from './display-info-contract'
import {displayTime} from '../helpers'
import {FactQuestions} from './fact-questions'
import {factMachineFindings} from './fact-answers-contract'
import type {ReadRound} from './round-reader'
import {conclusionLabel, executionLabel, reportLabel, resultLabel, resultTone, uncoveredLabel, type ResultTone} from './status-labels'

type Task = components['schemas']['AuditTask']
type Round = components['schemas']['Round']

export function AuditExcludedFiles({files}: {files: Task['files']}) {
  return <div className="space-y-5">{(['unselected', 'removed'] as const).map(selection => {
    const rows = files.filter(row => row.selection === selection)
    const title = selection === 'unselected' ? '未选择文件' : '已移除文件'
    return rows.length > 0 && <section key={selection} aria-label={title} className="space-y-2 border-t pt-4"><h2 className="text-sm font-semibold">{title}</h2><ItemGroup>{rows.map(row => <Item role="listitem" key={row.file.id} size="xs"><ItemContent className="min-w-0"><FileName name={row.file.original_filename}/><ItemDescription wrap>{selection === 'unselected' ? '本次未选择，不进入审核' : row.removal_reason || '移除原因未提供'}</ItemDescription></ItemContent></Item>)}</ItemGroup></section>
  })}</div>
}

export function AuditWorkspace({task, busy, readError, onReadSupport, onRead, onChange, renderOriginal, reportAdapter, notificationAdapter, onReportChange, onReportView, sharingAdapter, onEditDisplay, headerActions, onReaudit, readRound, taskRail}: {taskRail?: ReactNode; task: Task; busy: boolean; readError: string; onReadSupport: () => void; onRead: () => void; onChange: (task: Task) => void; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; notificationAdapter?: AuditNotificationAdapter; onReportChange: (task: Task) => void; onReportView: (open: boolean) => void; sharingAdapter?: AuditSharingAdapter; onEditDisplay?: (file: components['schemas']['AuditTaskFile']) => void; headerActions?: HTMLElement | null; onReaudit?: () => void; readRound?: ReadRound}) {
  const openSupport = useContext(SupportEntryContext)
  const queue = task.files.filter(row => row.selection === 'selected')
  const progressIssue = taskProgressError(task)
  const executionFailed = hasExecutionError(summarizeTask(task))
  const targets = queue.filter(row => row.match?.state !== 'reference'), references = queue.filter(row => row.match?.state === 'reference')
  const counts = taskResultCounts(task), readyReports = targets.filter(row => reportReady(task, row)).length
  const [selected, setSelected] = useState(() => new URL(location.href).searchParams.get('file') || '')
  const [allIssues, setAllIssues] = useState(false)
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
  const issueLimit = 12
  const shownIssueGroups = issues.slice(0, allIssues ? issues.length : issueLimit).reduce<{row: (typeof issues)[number]['row']; items: (typeof issues)[number]['check'][]}[]>((groups, {row, check}) => {
    const group = groups.find(item => item.row.file.id === row.file.id)
    if (group) group.items.push(check); else groups.push({row, items: [check]})
    return groups
  }, [])
  const issueLabel = (check: (typeof issues)[number]['check']) => check.execution === 'failed' ? resultLabel.ERR : check.coverage !== 'executed' ? uncoveredLabel : check.finding ? resultLabel[check.finding.status] : '结果未知'
  function openIssue(fileId: string, clauseId: string) {
    setSelected(fileId)
    const url = new URL(location.href)
    url.searchParams.set('file', fileId); url.searchParams.set('clause', clauseId); url.searchParams.delete('focus'); url.searchParams.set('checkFilter', 'attention'); url.searchParams.set('basis', '1')
    writeAuditAddress(url, 'push')
  }
  const running = task.state !== 'completed' && !progressIssue && itemsTotal > 0
  const overview = <section aria-label="全部文件概览" className="min-w-0 space-y-4 p-6">
    {running && <section aria-label="审核进度" className="space-y-2 rounded-lg border bg-card p-4"><div className="flex flex-wrap items-baseline justify-between gap-2"><p className="font-medium">正在审核</p><p className="text-sm tabular-nums text-muted-foreground">审核项 {itemsDone}/{itemsTotal} 已结束</p></div><Progress aria-label="审核项完成进度" value={Math.min(100, itemsDone / itemsTotal * 100)}/></section>}
    {issues.length > 0 && <section aria-label="需关注的审核项" className="space-y-3 rounded-lg border bg-card p-4"><h2 className="font-medium">需关注 · {issues.length} 项</h2>
      {/* Grouped by file, worst file first, so each row names only the clause. */}
      {shownIssueGroups.map(group => <section key={group.row.file.id} aria-label={group.row.file.original_filename} className="space-y-1"><h3 className="text-xs font-medium text-muted-foreground">{group.row.file.original_filename} · {group.items.length} 项</h3><ul className="divide-y">{group.items.map(check => <li key={check.rule_item.id}><Button variant="ghost" size="sm" className="h-auto min-h-8 w-full min-w-0 justify-start whitespace-normal" title={check.rule_item.id} onClick={() => openIssue(group.row.file.id, check.rule_item.id)}><span className={`size-2 shrink-0 rounded-full ${check.finding?.status === 'NG' && check.coverage === 'executed' && check.execution === 'completed' ? 'bg-destructive' : 'bg-warning'}`} aria-hidden="true"/><span className="min-w-0 flex-1 text-left text-sm">{`${issueLabel(check)} · ${clauseLabel(group.row.audit?.checks.map(item => item.rule_item) ?? [], check.rule_item.id)}`}</span><ArrowRight aria-hidden="true" className="text-muted-foreground"/></Button></li>)}</ul></section>)}
      {issues.length > issueLimit && <Button variant="link" size="sm" aria-expanded={allIssues} onClick={() => setAllIssues(value => !value)}>{allIssues ? `只看前 ${issueLimit} 项` : `查看全部 ${issues.length} 项`}</Button>}</section>}
    {!running && issues.length === 0 && <p className="text-sm text-muted-foreground">{task.state === 'completed' && !executionFailed && !progressIssue ? '没有需关注的审核项。从文件列表查看逐项结果和依据。' : '从文件列表查看各文件的状态。'}</p>}
    {Boolean(task.predecessor_id || task.successor_tasks?.length) && <div className="flex flex-wrap items-center gap-3">{task.predecessor_id && <Button variant="link" asChild><a href={task.predecessor_task_id ? taskAddress(task.predecessor_task_id) : `/?round=${encodeURIComponent(task.predecessor_id)}`}>查看前次审核与人工记录</a></Button>}{task.successor_tasks?.map(next => <Button key={next.id} variant="link" asChild><a href={taskAddress(next.id)}>关联重审 {next.number}</a></Button>)}</div>}
  </section>
  // Each card names its customer and rule, as in the task's file list.
  const fileMeta = (row: Task['files'][number]) => [fileDisplayValues(row).customer, row.audit?.rule.title ?? row.match?.rule?.title].filter(Boolean).join(' · ')
  const stateTone = (row: Task['files'][number], invalid: unknown): ResultTone | 'info' => row.match?.state === 'reference' ? 'outline' : invalid ? 'warning' : !row.audit ? 'outline' : row.audit.execution === 'review_failed' ? 'destructive' : row.audit.execution !== 'completed' ? 'info' : row.audit.conclusion ? resultTone(row.audit.conclusion) : 'warning'
  // The tag names the state; this line adds only the counts behind it.
  const countsLine = (row: Task['files'][number], invalid: unknown) => !row.audit || invalid ? '' : [
    row.audit.execution === 'completed' ? '' : `已结束 ${row.audit.progress.completed}/${row.audit.progress.total} 项`,
    row.audit.counts.ng_count > 0 ? `${resultLabel.NG} ${row.audit.counts.ng_count}` : '',
    row.audit.counts.unknown_count > 0 ? `${resultLabel.UNABLE} ${row.audit.counts.unknown_count}` : '',
    row.audit.counts.not_covered_count > 0 ? `${uncoveredLabel} ${row.audit.counts.not_covered_count}` : '',
    row.audit.counts.error_count > 0 ? `${resultLabel.ERR} ${row.audit.counts.error_count}` : '',
  ].filter(Boolean).join(' · ')
  const subject = subjectLabel(taskSubject(task))
  const fileQueue = <div className="space-y-3 p-3"><div className="flex items-center justify-between gap-2 px-1"><h2 className="text-base font-semibold">文件列表</h2><Button variant={selected ? "outline" : "soft"} size="sm" onClick={() => chooseFile("")}>全部文件概览</Button></div><nav aria-label="文件队列">{[{title: '审核文件', rows: targets}, {title: '比对资料', rows: references}].filter(group => group.rows.length).map(group => <section key={group.title} className="min-w-0 space-y-2"><h2 className="px-1 text-xs font-medium text-muted-foreground">{group.title} ({group.rows.length})</h2><div className="grid gap-2">{group.rows.map(row => {
    const invalid = resultConsistencyError(task, row)
    const state = row.match?.state === 'reference' ? '比对资料' : invalid ? '结果待确认' : row.audit ? row.audit.execution === 'completed' ? row.audit.conclusion ? conclusionLabel[row.audit.conclusion] ?? '结果未知' : '结果未知' : executionLabel[row.audit.execution] : '等待读取结果'
    return <div key={row.file.id} className={`overflow-hidden rounded-md border transition-colors ${file?.file.id === row.file.id ? 'border-primary bg-primary-soft' : 'bg-card hover:border-primary-line'}`}><Button size="row" aria-pressed={file?.file.id === row.file.id} aria-label={`${row.file.original_filename} ${state}${row.audit && !invalid ? ` ${row.audit.progress.completed}/${row.audit.progress.total} 项 · ${reportLabel[row.audit.report.state]}` : ''}`} variant="ghost" className="min-w-0" onClick={() => chooseFile(row.file.id)}><span className="grid min-w-0 gap-1"><span className="flex min-w-0 items-start justify-between gap-2"><FileName colored className="min-w-0 text-primary" name={row.file.original_filename}/><Badge shape="square" variant={stateTone(row, invalid)}>{state}</Badge></span>{fileMeta(row) && <span className="truncate pl-8 text-xs text-muted-foreground" title={fileMeta(row)}>{fileMeta(row)}</span>}{countsLine(row, invalid) && <span className={!invalid && row.audit?.conclusion === 'NG' ? 'pl-8 text-xs text-destructive' : 'pl-8 text-xs text-muted-foreground'}>{countsLine(row, invalid)}</span>}{row.audit && !invalid && row.audit.execution === 'completed' && row.audit.checks.some(reviewable) && <span className="pl-8 text-xs tabular-nums text-muted-foreground">人工复核 {row.audit.checks.filter(hasReview).length}/{row.audit.checks.filter(reviewable).length}</span>}</span></Button></div>
  })}</div></section>)}</nav><AuditExcludedFiles files={task.files}/></div>
  const actions = <div hidden={report !== null}><div className="flex shrink-0 items-center gap-2">{onReaudit && task.state === "completed" && <Button variant="outline" onClick={onReaudit}>发起重审</Button>}<Button variant="outline" aria-label="审核报告" onClick={() => showReport(file?.match?.state === 'reference' ? '' : file?.file.id || '')}><FileText/><span>审核报告</span>{readyReports > 0 && <span>({readyReports})</span>}</Button><Button variant="outline" size="icon" aria-label="读取审核结果" title="刷新审核结果" disabled={busy} onClick={onRead}><RefreshCw className={busy ? 'animate-spin' : ''}/></Button>{task.notification_enabled !== undefined && notificationAdapter && <CompletionNotification compact task={task} adapter={notificationAdapter} onChange={preference => onChange({...task, notification_enabled: preference.enabled, notification_revision: preference.revision})}/>}</div></div>
  return <section className="flex min-h-0 flex-1 flex-col">
    {headerActions && createPortal(actions, headerActions)}
    {report !== null && <div className="min-h-0 flex-1 overflow-y-auto"><AuditReports headerActions={headerActions} task={task} initialFile={report === '1' ? '' : report} onChoose={showReport} onRead={onRead} busy={busy} readError={readError} onReadSupport={onReadSupport} adapter={reportAdapter} onChange={onReportChange} sharingAdapter={sharingAdapter}/></div>}
    <div hidden={report !== null} className="flex min-h-0 flex-1 flex-col"><header className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b bg-card px-6 py-2"><div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"><h1 className={subject ? 'text-sm font-medium text-muted-foreground' : 'text-lg font-semibold'}>审核结果</h1>{subject && <><span aria-hidden="true" className="text-muted-foreground/60">/</span><p className="text-base font-semibold">{subject}</p></>}<Badge shape="square" variant={progressIssue ? 'warning' : task.state === 'completed' && executionFailed ? 'destructive' : task.state === 'completed' ? 'success' : 'info'}>{progressIssue ? '进度待确认' : task.state === 'completed' ? executionFailed ? '审核异常' : '审核完成' : '审核中'}</Badge><p role="status" className={task.state === 'completed' && !progressIssue ? 'sr-only' : 'text-xs text-muted-foreground'}>{progressIssue ? '任务进度待确认' : task.progress ? <>文件已结束 {task.progress.completed}/{task.progress.total}</> : null}</p><span className="text-xs tabular-nums text-muted-foreground">任务编号 {task.number} · 共 {targets.length} 份 · {displayTime(task.created_at)}</span><AuditTaskEstimate task={task} progressIssue={Boolean(progressIssue)}/>{references.length > 0 && <span className="text-xs text-muted-foreground">比对资料 {references.length} 份</span>}{counts && !file && task.state === 'completed' && <><span className="text-xs text-muted-foreground">共 {counts.items} 项</span><div className="flex flex-wrap gap-1.5"><Badge shape="square" variant={counts.ng_count ? 'destructive' : 'outline'}>{resultLabel.NG} {counts.ng_count}</Badge>{counts.error_count > 0 && <Badge shape="square" variant="warning">{resultLabel.ERR} {counts.error_count}</Badge>}{counts.unknown_count > 0 && <Badge shape="square" variant="warning">{resultLabel.UNABLE} {counts.unknown_count}</Badge>}{counts.not_covered_count > 0 && <Badge shape="square" variant="warning">{uncoveredLabel} {counts.not_covered_count}</Badge>}<Badge shape="square" variant="success">{resultLabel.PASS} {counts.pass_count}</Badge></div></>}</div>{!headerActions && actions}</header>
      {task.state === 'completed' && task.round_id && <FactQuestions key={`facts:${task.round_id}`} roundId={task.round_id} files={queue.map(row => row.file)} findings={queue.flatMap(row => factMachineFindings(row.audit?.checks))}/>}
      <div className="shrink-0 space-y-3 empty:hidden [&:not(:empty)]:border-b [&:not(:empty)]:px-6 [&:not(:empty)]:py-3">
      {progressIssue && <Alert variant="destructive"><AlertTitle>任务进度需要核对</AlertTitle><AlertDescription>{progressIssue} 各文件的有效依据仍保留，请重新读取审核结果。</AlertDescription></Alert>}
      {task.usage_notice && <Alert><AlertTitle>用量提示</AlertTitle><AlertDescription>{task.usage_notice}{openSupport && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '审核用量提示', facts: [{label: '任务标识', value: task.id}, {label: '任务编号', value: task.number}, ...(task.round_id ? [{label: '审核轮次', value: task.round_id}] : []), {label: '服务提示', value: task.usage_notice!}]})}>联系支持</Button>}</AlertDescription></Alert>}{readError && <Alert variant="destructive"><AlertTitle>结果暂时无法读取</AlertTitle><AlertDescription><p>{readError} 原有结果与选择仍保留。</p><Button variant="outline" size="sm" onClick={onReadSupport}>联系支持</Button></AlertDescription></Alert>}
      </div>
      <div className={`grid min-h-0 min-w-0 flex-1 ${taskRail && !file ? 'grid-cols-[18rem_20rem_minmax(0,1fr)]' : 'grid-cols-[20rem_minmax(0,1fr)]'}`}>{/* Tasks sit beside the overview; inside one file the width goes to its items and basis. */}{taskRail && !file && <aside aria-label="审核任务" className="min-w-0 overflow-y-auto border-r bg-card">{taskRail}</aside>}<aside className="min-w-0 overflow-y-auto border-r bg-card">{fileQueue}</aside><div className="min-w-0 overflow-y-auto">{file && task.round_id ? file.match?.state === 'reference' ? <section className="min-w-0 space-y-4 p-6" aria-label="比对资料原件"><h2 className="font-semibold"><FileName name={file.file.original_filename}/></h2>{renderOriginal?.(file.file, null, task.round_id)}</section> : <AuditReviewContent onReaudit={onReaudit} previous={previousFile?.audit ? {label: '上次审核', checks: previousFile.audit.checks} : null} key={`${task.round_id}:${file.file.id}`} file={file} roundId={task.round_id} taskId={task.id} frozenFileVersionIds={queue.map(row => row.file.id)} originals={queue.map(row => ({file_version_id: row.file.id, file: row.file, access: {state: 'allowed', reason: null}}))} renderOriginal={renderOriginal} onReport={() => showReport(file.file.id)} onEditDisplay={onEditDisplay ? () => onEditDisplay(file) : undefined} facts={[{label: '创建时间', value: displayTime(task.created_at)}]}/> : overview}</div></div>
    </div>
  </section>
}
