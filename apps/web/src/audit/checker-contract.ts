import {request} from '../api.ts'
import type {components, operations} from '../generated/openapi'

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
  if (receipt.operation_id !== input.operation_id || receipt.suite_key !== key || receipt.revision_id !== input.revision_id || receipt.force_unverified !== input.force_unverified || receipt.revision !== input.expected_revision + 1) throw new Error('发布结果与本次操作不一致，请查看原操作。')
  return receipt
}
