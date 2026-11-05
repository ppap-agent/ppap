import assert from 'node:assert/strict'
import test from 'node:test'
import {checkedAdoption, nativeCheckerReleaseAdapter, requiresUnverifiedConfirmation, verificationText, type CheckerRevision} from './checker-contract.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})

const candidate = (): CheckerRevision => ({id: 'revision-a', suite_key: 'control-plan', title: '控制计划检查', version: 1, diff: [], validation: {status: 'verified', total_samples: 3, passed_samples: 3, new_errors: 0, reasons: []}, files: {}, changed_paths: [], round_count: 0, rounds: []})
test('empty, insufficient, stale and regressed verification are not disguised as a full pass', () => {
  const row = candidate()
  assert.equal(requiresUnverifiedConfirmation(row), false)
  for (const changes of [{total_samples: 0, passed_samples: 0}, {new_errors: 1}, {passed_samples: 2}, {reasons: ['因为 PFMEA 检查已更新']}]) assert.equal(requiresUnverifiedConfirmation({...row, validation: {...row.validation, ...changes}}), true)
  assert.equal(requiresUnverifiedConfirmation({...row, validation: {...row.validation, status: 'stale'}}), true)
  assert.match(verificationText({...row.validation, total_samples: 1, passed_samples: 1}), /只验证了这一条，没有回归样本/)
  assert.match(verificationText({...row.validation, total_samples: 0, passed_samples: 0}), /没有验证样本/)
})
test('adoption receipt must match exact operation, suite, revision, consent and CAS revision', () => {
  const input = {revision_id: 'revision-a', expected_revision: 2, operation_id: 'op-a', force_unverified: true}
  const receipt = {...input, suite_key: 'suite-a', revision: 3, published_by: null, published_at: ''}
  assert.equal(checkedAdoption(receipt, 'suite-a', input), receipt)
  for (const changes of [{operation_id: 'other'}, {suite_key: 'other'}, {revision_id: 'other'}, {force_unverified: false}, {revision: 4}]) assert.throws(() => checkedAdoption({...receipt, ...changes}, 'suite-a', input), /回执/)
})
test('a lost publish response is not retried by the adapter', async () => {
  const original = globalThis.fetch, calls: string[] = []
  globalThis.fetch = async (url, init) => {calls.push(`${init?.method} ${String(url)}`); throw new Error('reply lost')}
  try {await assert.rejects(nativeCheckerReleaseAdapter.adopt('suite/a', {revision_id: 'r', expected_revision: 0, operation_id: 'op', force_unverified: false}), /reply lost/); assert.equal(calls.length, 1); assert.match(calls[0], /suites\/suite%2Fa\/active$/)} finally {globalThis.fetch = original}
})
