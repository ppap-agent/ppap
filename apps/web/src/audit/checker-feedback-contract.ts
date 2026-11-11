import {request} from '../api.ts'
import type {components} from '../generated/openapi'

export type RawFeedbackEvidence = Pick<components['schemas']['AuditEvidenceReference'], 'file_version_id' | 'locator' | 'excerpt'>
export type FeedbackEvidence = Omit<RawFeedbackEvidence, 'locator'> & {locator: NonNullable<RawFeedbackEvidence['locator']>}
export type CheckerFeedbackInput = Omit<components['schemas']['FeedbackCreate'], 'evidence'> & {evidence: FeedbackEvidence[]}
export type CheckerFeedback = Omit<components['schemas']['FeedbackResponse'], 'evidence'> & {evidence: FeedbackEvidence[]}
export type ValidationClause = {clause_id: string; expected: string; actual: string | null; baseline_actual: string | null; kind: string; regression: boolean; evidence_locatable: boolean}
export type ValidationCase = {case_id: string; feedback_ids?: string[]; split: 'training' | 'holdout'; passed: boolean; error_code: string | null; clauses: ValidationClause[]}
export type CheckerValidation = Pick<components['schemas']['ValidationView'], 'verified' | 'total_samples' | 'passed_samples' | 'new_errors'> & {reasons: string[]; cases: ValidationCase[]}
export type CheckerOptimization = Omit<components['schemas']['OptimizationReceipt'], 'report' | 'usage'> & {report: (Pick<components['schemas']['DevelopmentReport'], 'verified' | 'reasons' | 'error_code'> & {validation?: CheckerValidation | null; code_changed?: boolean | null}) | null; usage: {input_tokens?: number | null; output_tokens?: number | null; total_tokens?: number | null; source?: string; uncertain?: boolean}}
export type CheckerFeedbackView = Omit<components['schemas']['FeedbackView'], 'feedback' | 'optimizations'> & {feedback: CheckerFeedback[]; optimizations: CheckerOptimization[]}
export type SuiteFeedbackView = Omit<components['schemas']['SuiteFeedbackView'], 'feedback'> & {feedback: CheckerFeedback[]}
export type FeedbackGroup = components['schemas']['FeedbackGroup']
export type AffectedRoundsView = components['schemas']['AffectedRoundsView']
export type OptimizeInput = components['schemas']['OptimizationCreate']
export type FeedbackUpdate = Omit<components['schemas']['FeedbackUpdate'], 'evidence'> & {evidence: FeedbackEvidence[]}
export type FeedbackDeletion = components['schemas']['FeedbackDeletion']
export interface CheckerFeedbackAdapter {
  read(round: string): Promise<CheckerFeedbackView>
  samples(revision: string, after?: number): Promise<SuiteFeedbackView>
  affected?(revision: string, clause: string, excludeRound: string, after?: string): Promise<AffectedRoundsView>
  save(round: string, input: CheckerFeedbackInput): Promise<CheckerFeedback>
  optimize(revision: string, input: OptimizeInput): Promise<CheckerOptimization>
  operation(id: string): Promise<CheckerOptimization>
  edit(round: string, id: string, input: FeedbackUpdate): Promise<CheckerFeedback>
  remove(round: string, id: string, revision: number): Promise<FeedbackDeletion>
  stop(id: string): Promise<CheckerOptimization>
}
const encoded = encodeURIComponent
export const nativeCheckerFeedbackAdapter: CheckerFeedbackAdapter = {
  read: async round => checkedFeedbackView(await request(`/audit-rounds/${encoded(round)}/checker-feedback`), round),
  samples: (revision, after) => request(`/suite-revisions/${encoded(revision)}/checker-feedback${after ? `?after=${after}` : ''}`),
  affected: (revision, clause, round, after) => request(`/suite-revisions/${encoded(revision)}/affected-rounds?clause_id=${encoded(clause)}&exclude_round_id=${encoded(round)}${after ? `&after=${encoded(after)}` : ''}`),
  save: async (round, input) => checkedFeedback(await request(`/audit-rounds/${encoded(round)}/checker-feedback`, 'POST', input), input),
  optimize: async (revision, input) => checkedOptimization(await request(`/suite-revisions/${encoded(revision)}/optimizations`, 'POST', input), input.operation_id, revision),
  edit: async (round, id, input) => checkedFeedbackEdit(await request(`/audit-rounds/${encoded(round)}/checker-feedback/${encoded(id)}`, 'PUT', input), round, id, input),
  remove: async (round, id, revision) => checkedFeedbackDeletion(await request(`/audit-rounds/${encoded(round)}/checker-feedback/${encoded(id)}?expected_revision=${revision}`, 'DELETE'), id, revision),
  stop: async id => checkedOptimization(await request(`/checker-optimizations/${encoded(id)}/stop`, 'POST'), id),
  operation: async id => checkedOptimization(await request(`/checker-optimizations/${encoded(id)}`), id),
}
export function checkedFeedbackView(view: CheckerFeedbackView, round: string) {
  if (view.round_id !== round || !Array.isArray(view.feedback) || !Array.isArray(view.optimizations)) throw new Error('反馈读取与本轮审核不一致。')
  return view
}
export function checkedFeedback(saved: CheckerFeedback, input: CheckerFeedbackInput) {
  if (!saved.id || !saved.suite_revision_id || saved.operation_id !== input.operation_id || saved.file_version_id !== input.file_version_id || saved.rule_version_id !== input.rule_version_id || saved.rule_clause_id !== input.rule_clause_id || saved.original_status !== input.original_status || saved.result_attempt !== input.result_attempt || saved.expected_status !== input.expected_status || saved.rationale !== input.rationale || (saved.reason_category || 'unspecified') !== (input.reason_category || 'unspecified') || !sameEvidence(saved.evidence, input.evidence)) throw new Error('反馈结果与本次结果或正确答案不一致，请查看原操作。')
  return saved
}
export function checkedOptimization(value: CheckerOptimization, operation: string, revision?: string) {
  if (value.operation_id !== operation || revision && value.suite_revision_id !== revision || !['saved', 'queued', 'running', 'completed', 'failed', 'unknown', 'stopped'].includes(value.state)) throw new Error('优化结果与本次请求不一致，请查看原操作。')
  return value
}
export function optimizationSummary(report: CheckerValidation) {
  const clauses = report.cases.flatMap(row => row.clauses)
  return {fixed: clauses.filter(row => row.kind === 'match' && row.baseline_actual !== null && row.baseline_actual !== row.expected).length, regressions: clauses.filter(row => row.regression).length, unknown: clauses.filter(row => ['missing', 'UNABLE', 'mismatch'].includes(row.kind)).length, errors: clauses.filter(row => row.kind === 'ERR').length + report.cases.filter(row => row.error_code && !row.clauses.some(item => item.kind === 'ERR')).length, falsePass: clauses.filter(row => row.kind === 'missed_detection').length, falseNg: clauses.filter(row => row.kind === 'false_alarm').length}
}

/** Only public locator/excerpt fields cross the feedback API, never machine-only metadata. */
export function feedbackEvidence(rows: RawFeedbackEvidence[]): FeedbackEvidence[] {
  return rows.flatMap(row => row.locator ? [{file_version_id: row.file_version_id, locator: row.locator, excerpt: row.excerpt.slice(0, 500)}] : [])
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}'
  return JSON.stringify(value)
}
export function sameEvidence(a: FeedbackEvidence[], b: FeedbackEvidence[]) {return canonical(a) === canonical(b)}
export function manualEvidence(file: string, kind: 'xlsx_cell' | 'pdf_page' | 'docx_paragraph', sheet: string, position: string, excerpt: string): FeedbackEvidence | null {
  const value = position.trim().toUpperCase()
  if (!file || excerpt.length > 500) return null
  if (kind === 'xlsx_cell') return sheet.trim() && /^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(value) ? {file_version_id: file, locator: {kind, sheet: sheet.trim(), cell: value}, excerpt} : null
  const index = Number(value)
  if (!Number.isSafeInteger(index) || index < 1 || !/^[1-9][0-9]*$/.test(value)) return null
  return {file_version_id: file, locator: kind === 'pdf_page' ? {kind, page: index} : {kind, paragraph: index}, excerpt}
}

export function checkedFeedbackEdit(saved: CheckerFeedback, round: string, id: string, input: FeedbackUpdate) {
  if (saved.round_id !== round || saved.id !== id || saved.revision !== input.expected_revision + 1 || saved.expected_status !== input.expected_status || saved.rationale !== input.rationale || (saved.reason_category || 'unspecified') !== (input.reason_category || 'unspecified') || !sameEvidence(saved.evidence, input.evidence)) throw new Error('样本修改结果不一致，请读取当前样本。')
  return saved
}
export function checkedFeedbackDeletion(saved: FeedbackDeletion, id: string, revision: number) {
  if (saved.id !== id || saved.deleted !== true || saved.revision !== revision + 1) throw new Error('样本删除结果不一致，请读取当前样本。')
  return saved
}
export function feedbackSourceStatus(status: string, attempt: number | null): status is CheckerFeedbackInput['original_status'] {
  return ['PASS', 'NG', 'NA', 'UNABLE', 'ERR'].includes(status) && Number.isSafeInteger(attempt) && Number(attempt) >= 1
}
export function checkerReason(reason: string) {
  const messages: Record<string, string> = {no_validation_proof: '这个版本还没有用样本验证过。', checker_feedback_conflicting_answers: '同一原件条款存在不同正确答案，请先改错或移除冲突答案。', published_rules_changed: '已发布新规则，当前检查仍基于旧规则，需要重新验证。', checker_rule_confirmation_changed: '规则或样本已变化，请重新读取并核对正确答案。', checker_artifact_unavailable: '该审核尚无可复用的检查版本，请读取最新结果或联系支持。', checker_feedback_scope_mismatch: '反馈不属于这项检查，请读取最新样本。', checker_sample_limit: '样本数量超过当前支持范围，请先整理样本。', checker_operation_conflict: '该操作已绑定其他内容，请先查看原操作。', suite_inputs_changed: '本检查或关联检查已更新，需要重新验证。', oracle_rules_changed: '规则原文已修改，请在新规则下重新确认正确答案。', tests_not_run: '尚未运行验证样本。', oracle_changed: '正确答案或样本已修改，需要重新验证。', related_suite_changed: '关联检查已更新，需要重新验证。', development_incomplete: '本次修复未完整执行，不能认定验证通过。', checker_cancelled: '已请求停止。', model_unavailable: '服务暂时不可用，请稍后重试；如持续出现，请联系管理员。', model_configuration_changed: '系统设置已更新，本次操作未被接收。请重新开始。', checker_feedback_rules_changed: '原反馈中的条款已变更，请先核对正确答案。', checker_holdout_input_exposed: '独立验证样本与修复样本使用同一原件，需要补充不同原件。', checker_training_sample_required: '请先添加可用于修复的样本。', insufficient_samples: '样本不足，需要补充独立验证样本。', holdout_required: '缺少独立验证样本，不能认定整体通过。', no_independent_oracle: '还没有人工确认答案的样本，尚未验证。在审核结果中标出判错的条款后可以验证。', too_few_samples: '验证样本不足，或缺少独立验证样本，不能认定整体通过。', oracle_clause_coverage_incomplete: '部分条款还没有人工确认答案的样本。', candidate_rejected: '这个版本没有通过保存检查，不能采用。'}
  return messages[reason] || reason
}
export function latestOptimization(view: CheckerFeedbackView, key: string) {
  return view.optimizations.find(row => row.suite_key === key) || null
}

export function selectedFeedback(batch: CheckerFeedback[], ids: string[] | null, currentIds: string[]) {
  const selected = new Set(ids ?? currentIds.slice(-1))
  return batch.filter(row => selected.has(row.id))
}