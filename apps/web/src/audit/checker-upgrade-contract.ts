import {request} from '../api.ts'
import type {operations} from '../generated/openapi'
import type {CheckerActive, CheckerRevision} from './checker-contract.ts'

export type UpgradeChecker = NonNullable<operations['suite_official_upgrade_suites__suite_key__upgrades_post']['requestBody']>['content']['application/json']
export type PendingUpgrade = {scope: string; suite: string; input: UpgradeChecker}
export interface CheckerUpgradeAdapter {
  seen(suite: string, target: string): Promise<CheckerActive>
  upgrade(suite: string, input: UpgradeChecker): Promise<CheckerRevision>
  read(suite: string, input: UpgradeChecker): Promise<CheckerRevision>
}
const encoded = encodeURIComponent
export const nativeCheckerUpgradeAdapter: CheckerUpgradeAdapter = {
  seen: (suite, target) => request(`/suites/${encoded(suite)}/official-update/seen`, 'POST', {official_revision_id: target}),
  upgrade: async (suite, input) => checkedUpgrade(await request(`/suites/${encoded(suite)}/upgrades`, 'POST', input), suite, input),
  read: async (suite, input) => checkedUpgrade(await request(`/suites/${encoded(suite)}/upgrades/${encoded(input.operation_id)}`), suite, input),
}

export function checkedUpgrade(value: CheckerRevision, suite: string, input: UpgradeChecker) {
  const receipt = value.upgrade
  if (!value.id || [input.source_revision_id, input.target_revision_id].includes(value.id) || value.suite_key !== suite || value.base_revision_id !== input.target_revision_id || !receipt || receipt.operation_id !== input.operation_id || receipt.source_revision_id !== input.source_revision_id || receipt.target_revision_id !== input.target_revision_id || receipt.expected_revision !== input.expected_revision) throw new Error('升级回执与原操作不一致，请读取原操作。')
  return value
}

export function upgradeAvailable(active: Pick<CheckerActive, 'suite_key' | 'official_update_available' | 'latest_official_revision_id'>, revision: Pick<CheckerRevision, 'suite_key' | 'id' | 'tenant_id' | 'official_base_revision_id'>) {
  return Boolean(active.suite_key === revision.suite_key && (revision.tenant_id != null || active.official_update_available) && active.latest_official_revision_id && revision.official_base_revision_id && revision.official_base_revision_id !== active.latest_official_revision_id && revision.id !== active.latest_official_revision_id)
}

const fields = ['checkerUpgradeScope', 'checkerUpgrade', 'checkerUpgradeSuite', 'checkerUpgradeSource', 'checkerUpgradeTarget', 'checkerUpgradeExpected']
/** Save identity before the write; refresh may only read this operation, never resubmit it. */
export function upgradeAddress(url: URL, value: PendingUpgrade | null): URL {
  const next = new URL(url)
  for (const field of fields) next.searchParams.delete(field)
  if (value) {
    next.searchParams.set('checkerUpgradeScope', value.scope)
    next.searchParams.set('checkerUpgrade', value.input.operation_id)
    next.searchParams.set('checkerUpgradeSuite', value.suite)
    next.searchParams.set('checkerUpgradeSource', value.input.source_revision_id)
    next.searchParams.set('checkerUpgradeTarget', value.input.target_revision_id)
    next.searchParams.set('checkerUpgradeExpected', String(value.input.expected_revision))
  }
  return next
}
export function savedUpgrade(url: URL, suite: string, scope: string): PendingUpgrade | null {
  const params = url.searchParams, expected = params.get('checkerUpgradeExpected')
  if (params.get('checkerUpgradeScope') !== scope || params.get('checkerUpgradeSuite') !== suite || !params.get('checkerUpgrade') || !params.get('checkerUpgradeSource') || !params.get('checkerUpgradeTarget') || expected === null || !/^\d+$/.test(expected) || !Number.isSafeInteger(Number(expected))) return null
  return {scope, suite, input: {operation_id: params.get('checkerUpgrade')!, source_revision_id: params.get('checkerUpgradeSource')!, target_revision_id: params.get('checkerUpgradeTarget')!, expected_revision: Number(expected)}}
}
