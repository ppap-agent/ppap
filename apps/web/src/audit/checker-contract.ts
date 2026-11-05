import {request} from '../api.ts'

export type ValidationSummary = {total_samples: number; passed_samples: number; new_errors: number; reasons: string[]; status: 'verified' | 'unverified' | 'stale'; samples_not_rerun?: boolean}
export type AdoptionReceipt = {operation_id: string; suite_key: string; revision_id: string; revision: number; force_unverified: boolean; published_by: string | null; published_by_name?: string | null; published_at: string}
export type CheckerActive = {title: string; version: number | null; origin: 'official' | 'enterprise'; selection_mode: 'automatic_official' | 'pinned' | 'candidate_only'; suite_key: string; revision: number; current_revision_id: string | null; verification_status: string; force_unverified: boolean; validation: ValidationSummary; history: AdoptionReceipt[]}
export type CheckerRevision = {id: string; suite_key: string; title: string; version: number; diff: {path: string; before: string | null; after: string | null}[]; validation: ValidationSummary; files: Record<string, string>; changed_paths: string[]; round_count: number; rounds: {id: string; task_id: string | null; created_at: string}[]}
export type AdoptChecker = {revision_id: string; expected_revision: number; operation_id: string; force_unverified: boolean}
export interface CheckerReleaseAdapter {
  revision(id: string): Promise<CheckerRevision>
  active(key: string): Promise<CheckerActive>
  adopt(key: string, input: AdoptChecker): Promise<AdoptionReceipt>
}
const encoded = encodeURIComponent
export const nativeCheckerReleaseAdapter: CheckerReleaseAdapter = {
  revision: id => request(`/suite-revisions/${encoded(id)}`),
  active: key => request(`/suites/${encoded(key)}/active`),
  adopt: async (key, input) => checkedAdoption(await request(`/suites/${encoded(key)}/active`, 'PUT', input), key, input),
}
export function checkedAdoption(receipt: AdoptionReceipt, key: string, input: AdoptChecker) {
  if (receipt.operation_id !== input.operation_id || receipt.suite_key !== key || receipt.revision_id !== input.revision_id || receipt.force_unverified !== input.force_unverified || receipt.revision !== input.expected_revision + 1) throw new Error('发布回执与本次操作不一致，请读取原操作。')
  return receipt
}
export function verificationText(validation: ValidationSummary) {
  if (validation.total_samples === 0) return '没有验证样本，需要重新验证'
  if (validation.total_samples === 1) return `已验证 ${validation.passed_samples}/1 条。只验证了这一条，没有回归样本。`
  return `已验证 ${validation.passed_samples}/${validation.total_samples} 条 · 新增错误 ${validation.new_errors} 条`
}
export function requiresUnverifiedConfirmation(revision: CheckerRevision) {
  return revision.validation.status !== 'verified' || revision.validation.total_samples === 0 || revision.validation.passed_samples !== revision.validation.total_samples || revision.validation.new_errors > 0 || revision.validation.reasons.length > 0
}
export function definiteCheckerRejection(cause: unknown) {
  const status = (cause as {status?: number})?.status
  return status !== undefined && [400, 401, 403, 404, 409, 422].includes(status)
}
