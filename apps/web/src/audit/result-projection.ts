import type {components} from '../generated/openapi'

type Task = components['schemas']['AuditTask']
type TaskFile = components['schemas']['AuditTaskFile']
export type ReviewFile = Pick<TaskFile, 'file' | 'audit' | 'display_info'> & Partial<Pick<TaskFile, 'match'>>
export function checkScopeComplete(item: components['schemas']['AuditClauseEvaluation']): boolean {
  return !item.scope || item.scope.unknown === 0 && item.scope.checked === item.scope.total && (item.scope.total > 0 || item.finding?.status === 'NA')
}

/** Wire property order is irrelevant; array order and all snapshot values are not. */
export function sameSnapshot(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => sameSnapshot(value, right[index]))
  const a = Object.entries(left), b = Object.entries(right)
  return a.length === b.length && a.every(([key, value]) => Object.hasOwn(right, key) && sameSnapshot(value, (right as Record<string, unknown>)[key]))
}

export function sameFrozenFileInfo(before: TaskFile, after: TaskFile): boolean {
  return sameSnapshot(before.file, after.file) && sameSnapshot(before.match?.metadata, after.match?.metadata) && before.match?.document_type === after.match?.document_type
}

export function sameFrozenInputs(before: Task, after: Task): boolean {
  return before.id === after.id && before.round_id === after.round_id && before.analysis_mode === after.analysis_mode && before.files.length === after.files.length && before.files.every(file => after.files.some(next => sameFrozenFileInfo(file, next) && file.selection === next.selection && file.removal_reason === next.removal_reason && sameSnapshot(file.match?.rule, next.match?.rule)))
}

/** Reject a regressive snapshot rather than mixing old counters with new checks. */
export function progressRegressed(before: Task, after: Task): boolean {
  if (!before.round_id || before.round_id !== after.round_id) return false
  if (before.progress && (!after.progress || after.progress.completed < before.progress.completed)) return true
  return before.files.some(row => {
    if (row.selection !== 'selected' || !row.audit) return false
    const next = after.files.find(file => file.file.id === row.file.id)?.audit
    return !next || next.progress.completed < row.audit.progress.completed || ['completed', 'review_failed'].includes(row.audit.execution) && next.execution !== row.audit.execution || row.audit.checks.some(check => ['completed', 'failed'].includes(check.execution) && !next.checks.some(item => item.rule_item.id === check.rule_item.id && ['completed', 'failed'].includes(item.execution)))
  })
}

export function reportReference(task: Task, row: TaskFile): components['schemas']['AuditReportReference'] | null {
  if (!task.files.some(file => sameSnapshot(file, row))) return null
  const audit = row.audit
  return row.selection === 'selected' && !resultConsistencyError(task, row) && audit?.report.report_id && audit.result_attempt !== null && task.round_id === audit.round_id ? {task_id: task.id, round_id: audit.round_id, file_version_id: row.file.id, result_attempt: audit.result_attempt, report_id: audit.report.report_id} : null
}
export function reportReady(task: Task, row: TaskFile): boolean {
  const report = row.audit?.report
  return Boolean(reportReference(task, row) && report?.state === 'ready' && report.generated_at && report.artifact?.artifact_id && report.artifact.content_type === 'application/pdf' && report.artifact.byte_size > 0 && report.artifact.filename === `${row.file.original_filename}-审核报告.pdf`)
}
export function multipleDistinctReports(reports: components['schemas']['AuditReportReference'][]): boolean {
  return reports.length >= 2 && reports.every((reference, index) => reports.findIndex(row => sameSnapshot(row, reference)) === index)
}
export function packageOutcomeError(job: components['schemas']['AuditReportPackage']): string {
  const outcomes = job.outcomes
  if (outcomes === undefined) return ''
  if (!Array.isArray(outcomes) || outcomes.length !== job.reports.length || job.receipt.state !== 'accepted' || outcomes.some(row => !row || !['pending', 'prepared', 'failed'].includes(row.state) || row.reason !== null && typeof row.reason !== 'string' || row.state === 'failed' && !row.reason?.trim())) return '逐报告状态不完整或缺少有效失败原因。'
  if (outcomes.some(row => job.reports.filter(reference => sameSnapshot(reference, row.reference)).length !== 1) || job.reports.some(reference => outcomes.filter(row => sameSnapshot(reference, row.reference)).length !== 1)) return '逐报告状态与本次固定报告集合不一致。'
  return job.state === 'ready' && outcomes.some(row => row.state !== 'prepared') ? 'ZIP 声称已就绪，但仍有未准备完成的报告。' : ''
}

/** A task package may use an earlier selection, never another frozen result. */
export function taskPackageScopeError(task: Task): string {
  const job = task.report_package
  return job && (!multipleDistinctReports(job.reports) || job.reports.some(reference => !task.files.some(row => sameSnapshot(reportReference(task, row), reference)))) ? 'ZIP 引用与当前任务的冻结审核结果不一致。' : ''
}

/** Build a send payload only for the selected frozen, ready artifacts. */
export function shareContentFor(task: Task, reports: components['schemas']['AuditReportReference'][], mode: 'link' | 'file'): components['schemas']['AuditShareContent'] | null {
  if (!reports.length || new Set(reports.map(reference => reference.file_version_id)).size !== reports.length || reports.some(reference => !task.files.some(row => reportReady(task, row) && sameSnapshot(reportReference(task, row), reference)))) return null
  if (mode === 'link') return {kind: 'link', artifact: null, package_id: null}
  if (reports.length === 1) return {kind: 'pdf', artifact: structuredClone(task.files.find(row => row.file.id === reports[0].file_version_id)!.audit!.report.artifact!), package_id: null}
  const job = task.report_package
  if (job?.state === 'ready' && !packageOutcomeError(job) && job.receipt.state === 'accepted' && job.package_id && job.artifact?.artifact_id && job.artifact.content_type === 'application/zip' && job.artifact.byte_size > 0 && sameSnapshot(job.reports, reports)) return {kind: 'zip', artifact: structuredClone(job.artifact), package_id: job.package_id}
  return null
}

/** Fail closed on contradictory projections; this does not replace server validation. */
export function resultConsistencyError(task: Task, row: TaskFile): string {
  return frozenResultError(task.round_id, row, task.files.filter(file => file.selection === 'selected').map(file => file.file.id))
}

/** Task totals do not decide the validity of another file's evidence or report. */
export function taskProgressError(task: Task): string {
  if (!task.round_id) return ''
  const selected = task.files.filter(row => row.selection === 'selected')
  const ended = selected.filter(row => row.audit?.execution === 'review_failed' || row.audit?.execution === 'completed' && row.audit.checks.every(item => ['completed', 'failed'].includes(item.execution))).length
  if (task.state === 'completed' && ended !== selected.length) return '任务声称审核结束，但仍有未结束的文件。'
  if (task.state === 'completed' && selected.some(row => {
    const report = row.audit?.report, request = report?.generation_request
    return report && ['pending', 'generating'].includes(report.state) && !(request?.request_id && ['pending', 'accepted'].includes(request.state))
  })) return '任务声称审核结束，但仍有首次生成中的报告。'
  if (task.progress && (task.progress.total !== selected.length || task.progress.completed !== ended)) return '任务进度与已选文件状态不一致。'
  return ''
}

/** Validate a single authorized historical result without inventing a whole task. */
export function frozenResultError(roundId: string | null, row: ReviewFile, frozenFileVersionIds: string[], frozenRule = row.match?.rule): string {
  const audit = row.audit
  if (!audit) return ''
  if (audit.file_version_id !== row.file.id || audit.round_id !== roundId || !sameSnapshot(audit.rule, frozenRule)) return '审核结果与冻结文件或规则不一致。'
  const rules = audit.rule.body.kind === 'engine' ? audit.rule.body.rules : []
  if (!rules.length && audit.rule.body.kind === 'engine' || new Set(audit.checks.map(item => item.rule_item.id)).size !== audit.checks.length || audit.checks.length !== rules.length || audit.checks.some(item => !rules.some(rule => sameSnapshot(rule, item.rule_item)) || item.finding && item.finding.item_key !== item.rule_item.id) || audit.progress.total !== rules.length) return '审核项与冻结规则不一致。'
  if (audit.execution === 'completed' && audit.checks.some(item => ['queued', 'running'].includes(item.execution))) return '文件声称审核结束，但仍有未结束的审核项。'
  const frozen = new Set(frozenFileVersionIds)
  if (audit.checks.some(item => item.evidence.some(source => !frozen.has(source.file_version_id)))) return '来源证据不在本轮冻结文件范围内。'
  const valid = audit.checks.filter(item => item.execution === 'completed' && item.coverage === 'executed' && item.evidence_verified && item.finding)
  if (valid.some(item => item.finding?.status === 'PASS' && !checkScopeComplete(item))) return 'PASS 结论缺少完整的检查范围，未知不能视为满足。'
  const counts = {pass_count: 0, ng_count: 0, unknown_count: 0, not_covered_count: 0, error_count: 0}
  for (const item of audit.checks) {
    if (['queued', 'running'].includes(item.execution)) continue
    if (item.execution === 'failed' || item.finding?.status === 'ERR') counts.error_count += 1
    else if (item.coverage !== 'executed') counts.not_covered_count += 1
    else if (!item.evidence_verified || !item.finding || item.finding.status === 'UNABLE') counts.unknown_count += 1
    else if (item.finding.status === 'PASS') counts.pass_count += 1
    else if (item.finding.status === 'NG') counts.ng_count += 1
  }
  if (!sameSnapshot(counts, audit.counts)) return '审核计数与条目明细不一致。'
  const complete = rules.length > 0 && audit.execution === 'completed' && valid.length === rules.length && valid.every(item => ['PASS', 'NG', 'NA'].includes(item.finding!.status) && checkScopeComplete(item))
  if (audit.progress.completed !== audit.checks.filter(item => ['completed', 'failed'].includes(item.execution)).length) return '审核进度与条目状态不一致。'
  if (audit.conclusion === 'PASS' && (!complete || valid.some(item => item.finding?.status === 'NG'))) return 'PASS 结论缺少完整、已验证的审核依据。'
  if (audit.conclusion === 'NG' && !valid.some(item => item.finding?.status === 'NG')) return 'NG 结论没有对应的已验证不符合项。'
  if (audit.summary && audit.summary.conclusion !== audit.conclusion) return '总结与审核明细不一致，暂不展示总结和正式报告。'
  if (audit.report.state !== 'not_eligible' && (!complete || !['PASS', 'NG'].includes(audit.conclusion || ''))) return '未完整验证的文件不能生成正式 PASS/NG 报告。'
  return ''
}
