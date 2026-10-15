import type {components} from '../generated/openapi'

type Task = components['schemas']['AuditTask']
type TaskFile = components['schemas']['AuditTaskFile']
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

export function sameFrozenInputs(before: Task, after: Task): boolean {
  return before.id === after.id && before.round_id === after.round_id && before.analysis_mode === after.analysis_mode && before.files.length === after.files.length && before.files.every(file => after.files.some(next => sameSnapshot(file.file, next.file) && file.selection === next.selection && file.removal_reason === next.removal_reason && sameSnapshot(file.match?.rule, next.match?.rule)))
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

/** Build a send payload only for the selected frozen, ready artifacts. */
export function shareContentFor(task: Task, reports: components['schemas']['AuditReportReference'][], mode: 'link' | 'file'): components['schemas']['AuditShareContent'] | null {
  if (!reports.length || new Set(reports.map(reference => reference.file_version_id)).size !== reports.length || reports.some(reference => !task.files.some(row => reportReady(task, row) && sameSnapshot(reportReference(task, row), reference)))) return null
  if (mode === 'link') return {kind: 'link', artifact: null, package_id: null}
  if (reports.length === 1) return {kind: 'pdf', artifact: structuredClone(task.files.find(row => row.file.id === reports[0].file_version_id)!.audit!.report.artifact!), package_id: null}
  const job = task.report_package
  if (job?.state === 'ready' && job.receipt.state === 'accepted' && job.package_id && job.artifact?.artifact_id && job.artifact.content_type === 'application/zip' && job.artifact.byte_size > 0 && sameSnapshot(job.reports, reports)) return {kind: 'zip', artifact: structuredClone(job.artifact), package_id: job.package_id}
  return null
}

/** Fail closed on contradictory projections; this does not replace server validation. */
export function resultConsistencyError(task: Task, row: TaskFile): string {
  return frozenResultError(task.round_id, row, task.files.filter(file => file.selection === 'selected').map(file => file.file.id))
}

/** Validate a single authorized historical result without inventing a whole task. */
export function frozenResultError(roundId: string | null, row: TaskFile, frozenFileVersionIds: string[]): string {
  const audit = row.audit
  if (!audit) return ''
  if (audit.file_version_id !== row.file.id || audit.round_id !== roundId || !sameSnapshot(audit.rule, row.match?.rule)) return '审核结果与冻结文件或规则不一致。'
  const rules = audit.rule.body.kind === 'engine' ? audit.rule.body.rules : []
  if (!rules.length || new Set(audit.checks.map(item => item.rule_item.id)).size !== audit.checks.length || audit.checks.length !== rules.length || audit.checks.some(item => !rules.some(rule => sameSnapshot(rule, item.rule_item)) || item.finding && item.finding.item_key !== item.rule_item.id) || audit.progress.total !== rules.length) return '审核项与冻结规则不一致。'
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
  const complete = audit.execution === 'completed' && valid.length === rules.length && valid.every(item => ['PASS', 'NG', 'NA'].includes(item.finding!.status) && checkScopeComplete(item))
  if (audit.progress.completed !== audit.checks.filter(item => ['completed', 'failed'].includes(item.execution)).length) return '审核进度与条目状态不一致。'
  if (audit.conclusion === 'PASS' && (!complete || valid.some(item => item.finding?.status === 'NG'))) return 'PASS 结论缺少完整、已验证的审核依据。'
  if (audit.conclusion === 'NG' && !valid.some(item => item.finding?.status === 'NG')) return 'NG 结论没有对应的已验证不符合项。'
  if (audit.summary && audit.summary.conclusion !== audit.conclusion) return '总结与审核明细不一致，暂不展示总结和正式报告。'
  if (audit.report.state !== 'not_eligible' && (!complete || !['PASS', 'NG'].includes(audit.conclusion || ''))) return '未完整验证的文件不能生成正式 PASS/NG 报告。'
  return ''
}
