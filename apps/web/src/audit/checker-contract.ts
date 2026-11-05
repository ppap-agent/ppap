import {request} from '../api.ts'
import type {components, operations} from '../generated/openapi'

export type ValidationSummary = components['schemas']['SuiteValidation']
export type AdoptionReceipt = components['schemas']['SuitePublication']
export type CheckerActive = components['schemas']['SuiteActive']
export type CheckerRevision = components['schemas']['SuiteRevisionDetail']
export type AdoptChecker = NonNullable<operations['suites_publish_suites__suite_key__active_put']['requestBody']>['content']['application/json']
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
