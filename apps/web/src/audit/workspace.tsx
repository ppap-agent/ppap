// Stitch: projects/13261964020862199279/screens/fae9ff65452e4d899ded37b452347e17
import {useContext, useEffect, useId, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {FileText, RefreshCw} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {auditAddressChanged, readAuditRoute, taskAddress, writeAuditAddress} from './routes'
import {filterChecks, reviewed as hasReview, reviewable} from './review-navigation'
import {Progress} from '../components/ui/progress'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {CompletionNotification, type AuditNotificationAdapter} from './completion-notification'
import {Item, ItemContent, ItemDescription, ItemGroup} from '../components/ui/item'
import {FileName} from '../journey/file-name'
import {clauseLabel, pendingCount, pendingNote, reportReady, resultConsistencyError, taskProgressError, taskResultCounts} from './result-projection'
import {AuditReports} from './reports'
import {AuditReviewContent, useRegisteredCustomers, type OriginalRenderer} from './review-content'
import {customerFullName} from './customer-register'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {AuditTaskEstimate} from './task-estimate'
import {SupportEntryContext} from './support'
import {hasExecutionError, subjectLabel, summarizeTask, taskSubject} from './task-summary'
import {displayTime} from '../helpers'
import {FactQuestions} from './fact-questions'
import {factMachineFindings} from './fact-answers-contract'
import {conclusionLabel, executionLabel, reportLabel, resultLabel, resultTone, uncoveredLabel, type ResultTone} from './status-labels'
import {deepModeTitle, deepReviewNotice} from './deep-mode'
import {RunFinished, RunStatus} from './run-status'
import {LiveProgress} from './live-progress'
import type {LiveSteps} from './live-steps'

type Task = components['schemas']['AuditTask']
type Round = components['schemas']['Round']

export function AuditExcludedFiles({files}: {files: Task['files']}) {
  return <div className="space-y-5">{(['unselected', 'removed'] as const).map(selection => {
    const rows = files.filter(row => row.selection === selection)
    const title = selection === 'unselected' ? '未选择文件' : '已移除文件'
    return rows.length > 0 && <section key={selection} aria-label={title} className="space-y-2 border-t pt-4"><h2 className="text-sm font-semibold">{title}</h2><ItemGroup>{rows.map(row => <Item role="listitem" key={row.file.id} size="xs"><ItemContent className="min-w-0"><FileName name={row.file.original_filename}/><ItemDescription wrap>{selection === 'unselected' ? '本次未选择，不进入审核' : row.removal_reason || '移除原因未提供'}</ItemDescription></ItemContent></Item>)}</ItemGroup></section>
  })}</div>
}

/** Reads the original Round of a re-audit, for showing which files changed. */
export type ReadRound = (id: string, signal: AbortSignal) => Promise<components['schemas']['Round']>

export function AuditWorkspace({task, busy, readError, onReadSupport, onRead, onChange, renderOriginal, reportAdapter, notificationAdapter, onReportChange, onReportView, sharingAdapter, onEditDisplay, headerActions, onReaudit, readRound, onFileOpen, live = false, steps}: {live?: boolean; steps?: LiveSteps; onFileOpen?: (open: boolean) => void; task: Task; busy: boolean; readError: string; onReadSupport: () => void; onRead: () => void; onChange: (task: Task) => void; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; notificationAdapter?: AuditNotificationAdapter; onReportChange: (task: Task) => void; onReportView: (open: boolean) => void; sharingAdapter?: AuditSharingAdapter; onEditDisplay?: (file: components['schemas']['AuditTaskFile']) => void; headerActions?: HTMLElement | null; onReaudit?: () => void; readRound?: ReadRound}) {
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
  useEffect(() => {onFileOpen?.(Boolean(file)); return () => onFileOpen?.(false)}, [Boolean(file)])
  const previousFile = file && previousFiles.find(row => row.file.id === file.file.id || row.file.id === file.replacement_of_file_version_id || row.file.file_id === file.file.file_id)
  // The overview leads with what is still running and which finished items need a person.
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
    url.searchParams.set('file', fileId); url.searchParams.set('clause', clauseId); url.searchParams.delete('focus'); url.searchParams.set('checkFilter', 'attention')
    writeAuditAddress(url, 'push')
  }
  const running = task.state !== 'completed' && !progressIssue && itemsTotal > 0
  const runFile = task.run?.file_version_id ? targets.find(row => row.file.id === task.run?.file_version_id) : undefined
  // The round's end, once: a short check while the results are summarized, or when this page saw the run finish.
  const [justFinished, setJustFinished] = useState(false)
  const wasRunning = useRef(running)
  useEffect(() => {
    const finished = wasRunning.current && !running && task.state === 'completed' && !executionFailed
    wasRunning.current = running
    if (!finished) return
    setJustFinished(true)
    const timer = window.setTimeout(() => setJustFinished(false), 1200)
    return () => window.clearTimeout(timer)
  }, [running, task.state, executionFailed])
  const finishing = running && task.run?.stage === 'summary' || justFinished
  const overview = <section aria-label="全部文件概览" className="min-w-0 space-y-4 p-4">
    {finishing ? <RunFinished/> : running && (runFile && task.run?.file_version_id && task.run.index && task.run.total
      ? <RunStatus run={{...task.run, file_version_id: task.run.file_version_id, index: task.run.index, total: task.run.total}} deep={task.deep_mode} name={runFile.file.original_filename} steps={live ? steps : undefined}/>
      : <section aria-label="审核进度" className="rounded-lg border bg-card p-4"><LiveProgress label={task.deep_mode ? '正在深度审核' : '正在审核'} since={task.round_started_at} live={live}/></section>)}
    {issues.length > 0 && <div className="space-y-4">{shownIssueGroups.map(group => <section key={group.row.file.id} aria-label={group.row.file.original_filename} className="shrink-0 overflow-hidden rounded-lg border bg-card"><div className="px-4 py-3"><h2 className="min-w-0 font-medium"><FileName truncate name={group.row.file.original_filename}/></h2></div>
        <Table aria-label={`${group.row.file.original_filename} 需关注项`}><TableHeader variant="muted"><TableRow><TableHead className="w-24">结果</TableHead><TableHead className="w-20">条款</TableHead><TableHead>审核项</TableHead></TableRow></TableHeader><TableBody>{group.items.map(check => {
          const clauses = group.row.audit?.checks.map(item => item.rule_item) ?? []
          const clauseIndex = clauses.findIndex(item => item.id === check.rule_item.id)
          const clauseNo = clauseIndex < 0 ? '—' : `第 ${clauseIndex + 1} 条`
          const clauseName = clauseIndex < 0 ? clauseLabel(clauses, check.rule_item.id) : clauses[clauseIndex].category
          const openRow = () => openIssue(group.row.file.id, check.rule_item.id)
          return <TableRow key={check.rule_item.id} className="cursor-pointer" onClick={event => {if (!(event.target instanceof Element && event.target.closest('button'))) openRow()}}><TableCell className="align-top"><Badge shape="square" variant={check.finding?.status === 'NG' && check.coverage === 'executed' && check.execution === 'completed' ? 'destructive' : 'warning'}>{issueLabel(check)}</Badge></TableCell><TableCell className="align-top" title={clauseIndex < 0 ? check.rule_item.id : undefined}><span className="tabular-nums text-muted-foreground">{clauseNo}</span></TableCell><TableCell className="whitespace-normal align-top">{/* The row is a mouse target; this button is the keyboard and screen-reader entry. */}<Button variant="link" size="sm" className="h-auto justify-start text-left whitespace-normal" title={clauseName} onClick={openRow}>{clauseName}</Button></TableCell></TableRow>
        })}</TableBody></Table></section>)}
      {issues.length > issueLimit && <Button variant="link" size="sm" aria-expanded={allIssues} onClick={() => setAllIssues(value => !value)}>{allIssues ? `只看前 ${issueLimit} 项` : `查看全部 ${issues.length} 个需关注项`}</Button>}</div>}
    {!running && issues.length === 0 && <p className="text-sm text-muted-foreground">{task.state === 'completed' && !executionFailed && !progressIssue ? '没有需关注的审核项。从文件列表查看逐项结果和依据。' : '从文件列表查看各文件的状态。'}</p>}
    {Boolean(task.predecessor_id || task.successor_tasks?.length) && <div className="flex flex-wrap items-center gap-3">{task.predecessor_id && <Button variant="link" asChild><a href={task.predecessor_task_id ? taskAddress(task.predecessor_task_id) : `/?round=${encodeURIComponent(task.predecessor_id)}`}>查看前次审核与人工记录</a></Button>}{task.successor_tasks?.map(next => <Button key={next.id} variant="link" asChild><a href={taskAddress(next.id)}>关联重审 {next.number}</a></Button>)}</div>}
  </section>
  // Each card names its customer and rule, as in the task's file list.
  // A file's standing counts clauses settled by confirmed original facts; reports keep the machine conclusion.
  const fileConclusion = (audit: NonNullable<Task['files'][number]['audit']>) => audit.confirmed_conclusion ?? audit.conclusion
  const stateTone = (row: Task['files'][number], invalid: unknown): ResultTone | 'info' => row.match?.state === 'reference' ? 'outline' : invalid ? 'warning' : !row.audit ? 'outline' : row.audit.execution === 'review_failed' ? 'destructive' : row.audit.execution !== 'completed' ? 'info' : fileConclusion(row.audit) ? resultTone(fileConclusion(row.audit)) : 'warning'
  const queueStatusLabel = (state: string) => state === '等待读取结果' ? '待读取' : state
  // While the round runs, a file's place in it: the stage and the file under way are named once, in the status line.
  const runLabel = {pending: '待处理', prepared: '已准备', processing: '处理中', done: '已完成'} as const
  const runTone = {pending: 'outline', prepared: 'outline', processing: 'info', done: 'success'} as const
  const toneRail: Record<ResultTone | 'info', string> = {destructive: 'bg-destructive', warning: 'bg-warning', success: 'bg-success', info: 'bg-info', outline: 'bg-border'}
  // One secondary line: the exception, otherwise the work still open on this file.
  const queueNote = (row: Task['files'][number], invalid: unknown) => {
    const notice = deepReviewNotice(row.deep_review)
    if (notice) return {text: notice, className: 'text-xs text-warning'}
    if (!row.audit || invalid) return null
    if (row.audit.execution !== 'completed') return {text: `已结束 ${row.audit.progress.completed}/${row.audit.progress.total} 项`, className: 'text-xs tabular-nums text-muted-foreground'}
    // What still needs a person is the server's standing, the same the Feishu Project tab counts; review progress follows.
    // 待审核 clauses are named once, beside the file label; 待处理 counts the rest that need a person.
    const waiting = pendingNote(row.audit)
    const pending = row.audit.checks.filter(check => check.attention).length - (waiting ? pendingCount(row.audit) : 0)
    const open = row.audit.checks.filter(reviewable)
    const done = row.audit.checks.filter(hasReview).length
    const parts = [waiting, pending ? `待处理 ${pending}` : '', open.length ? `已复核 ${done}/${open.length}` : ''].filter(Boolean)
    return parts.length ? {text: parts.join(' · '), className: pending || waiting ? 'text-xs font-medium tabular-nums text-foreground' : 'text-xs tabular-nums text-muted-foreground'} : null
  }
  // Each queue button is named by its file; state, progress and report status describe it.
  const queueId = useId()
  const customers = useRegisteredCustomers()
  // P5 shows the registered full name throughout (PRD §1.4 ①③).
  const subject = subjectLabel(taskSubject(task), value => customerFullName(customers, value))
  const fileGroups = [{title: '审核文件', rows: targets}, {title: '比对资料', rows: references}].filter(group => group.rows.length)
  const fileQueue = <div className="space-y-3 p-3"><div className="flex items-center justify-between gap-2 px-1"><h2 className="text-base font-semibold">文件列表</h2><Button variant={selected ? 'outline' : 'soft'} size="sm" aria-pressed={!selected} onClick={() => chooseFile('')}>全部文件概览</Button></div><nav aria-label="文件队列">{fileGroups.map(group => <section key={group.title} className="min-w-0 space-y-2">{fileGroups.length > 1 && <h2 className="px-1 text-xs font-medium text-muted-foreground">{group.title}</h2>}<div className="grid gap-2">{group.rows.map(row => {
    const invalid = resultConsistencyError(task, row)
    const state = row.match?.state === 'reference' ? '比对资料' : invalid ? '结果待确认' : row.audit ? row.audit.execution === 'completed' ? fileConclusion(row.audit) ? conclusionLabel[fileConclusion(row.audit)!] ?? '结果未知' : '结果未知' : executionLabel[row.audit.execution] : '等待读取结果'
    const place = running && !invalid ? row.run_state : undefined
    const badge = place ? runLabel[place] : queueStatusLabel(state)
    const tone = place ? runTone[place] : stateTone(row, invalid)
    const note = place ? null : queueNote(row, invalid)
    const statusId = `${queueId}-${row.file.id}`
    const current = file?.file.id === row.file.id
    return <div key={row.file.id} className={`relative overflow-hidden rounded-md border transition-colors ${current ? 'border-primary bg-primary-soft' : 'bg-card hover:border-primary-line'}`}><span aria-hidden="true" className={`absolute inset-y-0 left-0 w-0.5 ${toneRail[tone]}`}/><span id={statusId} hidden>{place ? badge : <>{state}{row.audit && !invalid ? ` ${row.audit.progress.completed}/${row.audit.progress.total} 项 · ${reportLabel[row.audit.report.state]}` : ''}</>}</span><Button size="row" aria-pressed={current} aria-label={row.file.original_filename} aria-describedby={statusId} variant="ghost" className="min-w-0" onClick={() => chooseFile(row.file.id)}><span className="grid min-w-0 flex-1 gap-1 pl-1"><span className="flex min-w-0 items-center justify-between gap-2"><FileName colored truncate className="min-w-0 text-primary" name={row.file.original_filename}/><Badge shape="square" variant={tone} title={place || badge === state ? undefined : state}>{badge}</Badge></span>{note && <span className={note.className}>{note.text}</span>}</span></Button></div>
  })}</div></section>)}</nav><AuditExcludedFiles files={task.files}/></div>
  const actions = <div hidden={report !== null}><div className="flex shrink-0 items-center gap-2">{onReaudit && task.state === "completed" && <Button variant="outline" onClick={onReaudit}>发起重审</Button>}<Button variant="outline" aria-label="审核报告" onClick={() => showReport(file?.match?.state === 'reference' ? '' : file?.file.id || '')}><FileText/><span>审核报告</span>{readyReports > 0 && <span>({readyReports})</span>}</Button><Button variant="outline" size="icon" aria-label="读取审核结果" title="刷新审核结果" disabled={busy} onClick={onRead}><RefreshCw className={busy ? 'animate-spin motion-reduce:animate-none' : ''}/></Button>{task.notification_enabled !== undefined && notificationAdapter && task.creator.mine && <CompletionNotification compact task={task} adapter={notificationAdapter} onChange={preference => onChange({...task, notification_enabled: preference.enabled, notification_revision: preference.revision})}/>}</div></div>
  return <section className="flex min-h-0 flex-1 flex-col">
    {headerActions && createPortal(actions, headerActions)}
    {report !== null && <div className="min-h-0 flex-1 overflow-y-auto"><AuditReports headerActions={headerActions} task={task} initialFile={report === '1' ? '' : report} onChoose={showReport} onRead={onRead} busy={busy} readError={readError} onReadSupport={onReadSupport} adapter={reportAdapter} onChange={onReportChange} sharingAdapter={sharingAdapter}/></div>}
    <div hidden={report !== null} className="flex min-h-0 flex-1 flex-col"><header className="flex shrink-0 flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b bg-card px-6 py-3"><div className="min-w-0 space-y-1"><div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"><h1 className={subject ? 'text-sm font-medium text-muted-foreground' : 'text-lg font-semibold'}>审核结果</h1>{subject && <><span aria-hidden="true" className="text-muted-foreground/60">/</span><p className="text-base font-semibold">{subject}</p></>}<Badge shape="square" variant={progressIssue ? 'warning' : task.state === 'completed' && executionFailed ? 'destructive' : task.state === 'completed' ? 'success' : 'info'}>{progressIssue ? '进度待确认' : task.state === 'completed' ? executionFailed ? '审核异常' : '审核完成' : '审核中'}</Badge><p role="status" className={task.state === 'completed' && !progressIssue ? 'sr-only' : 'text-xs text-muted-foreground'}>{progressIssue ? '任务进度待确认' : task.progress ? <>文件已结束 {task.progress.completed}/{task.progress.total}</> : null}</p>{task.deep_mode && <Badge shape="square" variant="secondary">{deepModeTitle}</Badge>}</div><div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs tabular-nums text-muted-foreground"><span>{task.creator.mine ? '' : `${task.creator.name || '同事'} 发起 · `}共 {targets.length} 份 · {displayTime(task.created_at)}</span>{task.source && <a href={task.source.url} target="_blank" rel="noopener noreferrer" className="text-primary underline-offset-4 hover:underline">回到飞书项目工作项</a>}{references.length > 0 && <span>比对资料 {references.length} 份</span>}<AuditTaskEstimate task={task} progressIssue={Boolean(progressIssue)}/></div></div>{(counts && !file && task.state === 'completed' || !headerActions) && <div className="flex shrink-0 flex-wrap items-center justify-end gap-3">{counts && !file && task.state === 'completed' && <div className="flex flex-wrap items-center gap-1.5"><span className="text-xs text-muted-foreground">共 {counts.items} 项</span><Badge shape="square" variant={counts.ng_count ? 'destructive' : 'outline'}>{resultLabel.NG} {counts.ng_count}</Badge>{counts.error_count > 0 && <Badge shape="square" variant="warning">{resultLabel.ERR} {counts.error_count}</Badge>}{counts.unknown_count > 0 && <Badge shape="square" variant="warning">{resultLabel.UNABLE} {counts.unknown_count}</Badge>}{counts.not_covered_count > 0 && <Badge shape="square" variant="warning">{uncoveredLabel} {counts.not_covered_count}</Badge>}<Badge shape="square" variant="success">{resultLabel.PASS} {counts.pass_count}</Badge></div>}{!headerActions && actions}</div>}</header>
      {task.state === 'completed' && task.round_id && <FactQuestions key={`facts:${task.round_id}`} editable={task.creator.mine} roundId={task.round_id} files={queue.map(row => row.file)} findings={queue.flatMap(row => factMachineFindings(row.audit?.checks))}/>}
      <div className="shrink-0 space-y-3 empty:hidden [&:not(:empty)]:border-b [&:not(:empty)]:px-6 [&:not(:empty)]:py-3">
      {progressIssue && <Alert variant="destructive"><AlertTitle>任务进度需要核对</AlertTitle><AlertDescription>{progressIssue} 各文件的有效依据仍保留，请重新读取审核结果。</AlertDescription></Alert>}
      {task.usage_notice && <Alert><AlertTitle>用量提示</AlertTitle><AlertDescription><p>{task.usage_notice}</p>{openSupport && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '审核用量提示', facts: [{label: '任务标识', value: task.id}, {label: '任务编号', value: task.number}, ...(task.round_id ? [{label: '审核轮次', value: task.round_id}] : []), {label: '服务提示', value: task.usage_notice!}]})}>联系支持</Button>}</AlertDescription></Alert>}{readError && <Alert variant="destructive"><AlertTitle>结果暂时无法读取</AlertTitle><AlertDescription><p>{readError} 原有结果与选择仍保留。</p><Button variant="outline" size="sm" onClick={onReadSupport}>联系支持</Button></AlertDescription></Alert>}
      </div>
      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-[20rem_minmax(0,1fr)]"><aside className="min-w-0 overflow-y-auto border-r bg-card">{fileQueue}</aside><div className="min-w-0 overflow-y-auto">{file && task.round_id ? file.match?.state === 'reference' ? <section className="min-w-0 space-y-4 p-6" aria-label="比对资料原件"><h2 className="font-semibold"><FileName name={file.file.original_filename}/></h2>{renderOriginal?.(file.file, null, task.round_id)}</section> : <AuditReviewContent editable={task.creator.mine} previous={previousFile?.audit ? {label: '上次审核', checks: previousFile.audit.checks} : null} key={`${task.round_id}:${file.file.id}`} file={file} roundId={task.round_id} taskId={task.id} frozenFileVersionIds={queue.map(row => row.file.id)} originals={queue.map(row => ({file_version_id: row.file.id, file: row.file, access: {state: 'allowed', reason: null}}))} renderOriginal={renderOriginal} onEditDisplay={onEditDisplay && task.creator.mine ? () => onEditDisplay(file) : undefined} facts={[{label: '创建时间', value: displayTime(task.created_at)}]}/> : overview}</div></div>
    </div>
  </section>
}
