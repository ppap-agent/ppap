import {request} from '../api.ts'
import type {components} from '../generated/openapi'

export type FeedbackEvidence = Pick<components['schemas']['AuditEvidenceReference'], 'file_version_id' | 'locator' | 'excerpt'>
export type CheckerFeedbackInput = {operation_id: string; file_version_id: string; rule_version_id: string; rule_clause_id: string; original_status: string; result_attempt: number | null; expected_status: 'PASS' | 'NG' | 'NA'; rationale: string; evidence: FeedbackEvidence[]}
export type CheckerFeedback = CheckerFeedbackInput & {id: string; suite_revision_id: string; revision?: number}
export type ValidationClause = {clause_id: string; expected: string; actual: string | null; baseline_actual: string | null; kind: string; regression: boolean; evidence_locatable: boolean}
export type ValidationCase = {case_id: string; split: 'training' | 'holdout'; passed: boolean; error_code: string | null; clauses: ValidationClause[]}
export type CheckerValidation = {verified: boolean; reasons: string[]; cases: ValidationCase[]; total_samples: number; passed_samples: number; new_errors: number}
export type CheckerOptimization = {operation_id: string; suite_revision_id: string; state: 'queued' | 'running' | 'completed' | 'failed' | 'unknown'; candidate_revision_id: string | null; report: CheckerValidation | null; usage: {input_tokens?: number | null; output_tokens?: number | null; total_tokens?: number | null; state?: string} | null; failure: string | null}
export type CheckerFeedbackView = {round_id: string; feedback: CheckerFeedback[]; optimizations: CheckerOptimization[]}
export type OptimizeInput = {operation_id: string; feedback_ids: string[]; action: 'save_only' | 'run_tests'; consume_model: boolean}
export interface CheckerFeedbackAdapter {
  read(round: string): Promise<CheckerFeedbackView>
  save(round: string, input: CheckerFeedbackInput): Promise<CheckerFeedback>
  optimize(revision: string, input: OptimizeInput): Promise<CheckerOptimization>
  operation(id: string): Promise<CheckerOptimization>
}
const encoded = encodeURIComponent
export const nativeCheckerFeedbackAdapter: CheckerFeedbackAdapter = {
  read: async round => checkedFeedbackView(await request(`/audit-rounds/${encoded(round)}/checker-feedback`), round),
  save: async (round, input) => checkedFeedback(await request(`/audit-rounds/${encoded(round)}/checker-feedback`, 'POST', input), input),
  optimize: async (revision, input) => checkedOptimization(await request(`/suite-revisions/${encoded(revision)}/optimizations`, 'POST', input), input.operation_id, revision),
  operation: async id => checkedOptimization(await request(`/checker-optimizations/${encoded(id)}`), id),
}
export function checkedFeedbackView(view: CheckerFeedbackView, round: string) {
  if (view.round_id !== round || !Array.isArray(view.feedback) || !Array.isArray(view.optimizations)) throw new Error('反馈读取与本轮审核不一致。')
  return view
}
export function checkedFeedback(saved: CheckerFeedback, input: CheckerFeedbackInput) {
  if (!saved.id || !saved.suite_revision_id || saved.operation_id !== input.operation_id || saved.file_version_id !== input.file_version_id || saved.rule_version_id !== input.rule_version_id || saved.rule_clause_id !== input.rule_clause_id || saved.expected_status !== input.expected_status || saved.rationale !== input.rationale || JSON.stringify(saved.evidence) !== JSON.stringify(input.evidence)) throw new Error('反馈回执与本次结果或正确答案不一致，请读取原操作。')
  return saved
}
export function checkedOptimization(value: CheckerOptimization, operation: string, revision?: string) {
  if (value.operation_id !== operation || revision && value.suite_revision_id !== revision || !['queued', 'running', 'completed', 'failed', 'unknown'].includes(value.state)) throw new Error('优化回执与本次请求不一致，请读取原操作。')
  return value
}
export function optimizationSummary(report: CheckerValidation) {
  const clauses = report.cases.flatMap(row => row.clauses)
  return {fixed: clauses.filter(row => row.kind === 'match' && row.baseline_actual !== null && row.baseline_actual !== row.expected).length, regressions: clauses.filter(row => row.regression).length, unknown: clauses.filter(row => ['missing', 'UNABLE'].includes(row.kind)).length, errors: clauses.filter(row => row.kind === 'ERR').length, falsePass: clauses.filter(row => row.kind === 'missed_detection').length, falseNg: clauses.filter(row => row.kind === 'false_alarm').length}
}
