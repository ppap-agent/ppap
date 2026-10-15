import type {components} from '../generated/openapi'

type Task = components['schemas']['AuditTask']
type TaskFile = components['schemas']['AuditTaskFile']

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

/** Fail closed on contradictory projections; this does not replace server validation. */
export function resultConsistencyError(task: Task, row: TaskFile): string {
  const audit = row.audit
  if (!audit) return ''
  if (audit.file_version_id !== row.file.id || audit.round_id !== task.round_id || !sameSnapshot(audit.rule, row.match?.rule)) return '审核结果与冻结文件或规则不一致。'
  const rules = audit.rule.body.kind === 'engine' ? audit.rule.body.rules : []
  if (!rules.length || new Set(audit.checks.map(item => item.rule_item.id)).size !== audit.checks.length || audit.checks.length !== rules.length || audit.checks.some(item => !rules.some(rule => sameSnapshot(rule, item.rule_item))) || audit.progress.total !== rules.length) return '审核项与冻结规则不一致。'
  const frozen = new Set(task.files.filter(file => file.selection === 'selected').map(file => file.file.id))
  if (audit.checks.some(item => item.evidence.some(source => !frozen.has(source.file_version_id)))) return '来源证据不在本轮冻结文件范围内。'
  const valid = audit.checks.filter(item => item.execution === 'completed' && item.coverage === 'executed' && item.evidence_verified && item.finding)
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
  const complete = audit.execution === 'completed' && valid.length === rules.length && valid.every(item => ['PASS', 'NG', 'NA'].includes(item.finding!.status))
  if (audit.progress.completed !== audit.checks.filter(item => ['completed', 'failed'].includes(item.execution)).length) return '审核进度与条目状态不一致。'
  if (audit.conclusion === 'PASS' && (!complete || valid.some(item => item.finding?.status === 'NG'))) return 'PASS 结论缺少完整、已验证的审核依据。'
  if (audit.conclusion === 'NG' && !valid.some(item => item.finding?.status === 'NG')) return 'NG 结论没有对应的已验证不符合项。'
  if (audit.summary && audit.summary.conclusion !== audit.conclusion) return '总结与审核明细不一致，暂不展示总结和正式报告。'
  if (audit.report.state !== 'not_eligible' && (!complete || !['PASS', 'NG'].includes(audit.conclusion || ''))) return '未完整验证的文件不能生成正式 PASS/NG 报告。'
  return ''
}
