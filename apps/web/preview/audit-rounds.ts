import type {components} from '../src/generated/openapi'
import type {AuditTask} from '../src/audit/intake'

export function fixtureReviewRound(task: AuditTask): components['schemas']['Round'] {
  const files = task.files.filter(row => row.selection === 'selected')
  return {id: task.round_id!, execution: 'completed', result_attempt: 1, created_at: task.created_at, predecessor_id: null, source_draft_id: task.source_draft_id, analysis_mode: task.analysis_mode, writeback: {available: false}, progress: null, failure: null,
    inputs: files.map(row => ({file_id: row.file.file_id, file_version_id: row.file.id, original_filename: row.file.original_filename, status: row.file.status, role: 'target', rule_version_id: row.match!.rule!.id, rule: row.match!.rule})),
    findings: files.flatMap(row => row.audit!.checks.filter(check => check.finding).map(check => ({...check.finding!, item_key: check.review_item_key!, review: check.review ?? check.finding!.review}))),
    coverage: {rule_items: files.flatMap(row => row.audit!.checks.filter(check => !check.finding).map(check => ({...check.rule_item, file_version_id: row.file.id, coverage: check.coverage, review: check.review})))},
  }
}

/** Fixed historical records for the public reader, never a live Round. */
export function fixtureRound(task: AuditTask, id: string): components['schemas']['Round'] {
  const files: components['schemas']['AuditRoundReadFile'][] = task.files.map((row, index) => {
    const version = index === 2 ? 'history-version-reference' : `history-version-${index}`
    const rule = index === 2 ? null : index === 1 ? {id: 'history-presence', title: '文件存在', body: {kind: 'presence' as const}} : row.match!.rule!
    const finding: components['schemas']['Finding'] = {item_key: `${version}:engine:1`, item: index ? '待审文件版本已就绪' : '旧引擎频次检查', classification: '', priority: null, status: index ? 'PASS' : 'NG', suggestions: '', locator: {file_version_id: version, locator: 'Sheet1!A1'}, review: index ? null : {judgment: 'disagree', rationale: '历史人工答复：频次已经核对。', next_step: '', version: 1, citations: [{file_version_id: 'history-version-reference', location: '旧流程图第 2 页', excerpt: '本轮原始流程'}]}}
    return {file: {...row.file, id: version}, rule, role: index === 2 ? 'reference' as const : 'target' as const, audit: index === 2 ? null : {...row.audit!, execution: 'completed', failure: null, round_id: id, file_version_id: version, rule: rule!, checks: [], unmapped_findings: [finding], progress: {completed: 0, total: 0}, counts: {pass_count: 0, ng_count: 0, unknown_count: 0, not_covered_count: 0, error_count: 0}, conclusion: 'UNKNOWN' as const, summary: null, report: {state: 'not_eligible' as const, report_id: null, reason: '历史依据待核对。'}}}
  })
  const first = files[0]
  first.audit!.checks = task.files[0].audit!.checks.map(check => ({...check, finding: null, evidence: [], evidence_verified: false, coverage: 'unsupported', execution: 'completed'}))
  first.audit!.progress = {completed: first.audit!.checks.length, total: first.audit!.checks.length}
  first.audit!.counts.not_covered_count = first.audit!.checks.length
  return {id, execution: 'completed', coverage: {}, predecessor_id: null, source_draft_id: null, result_attempt: 1, created_at: '2026-10-15T00:00:00Z', analysis_mode: 'standard', writeback: {available: false}, progress: null, failure: null, inputs: files.map(row => ({file_id: row.file.file_id, file_version_id: row.file.id, original_filename: row.file.original_filename, status: row.file.status, role: row.role, rule_version_id: row.rule?.id || null, rule: row.rule})), findings: files.flatMap(row => row.audit?.unmapped_findings || []), read_view: {files, unassigned_findings: []}}
}
