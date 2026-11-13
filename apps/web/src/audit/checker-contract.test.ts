import assert from 'node:assert/strict'
import test from 'node:test'
import {checkedAdoption, nativeCheckerReleaseAdapter} from './checker-contract.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})

test('adoption receipt must match exact operation, suite, revision, consent and CAS revision', () => {
  const input = {revision_id: 'revision-a', expected_revision: 2, operation_id: 'op-a', force_unverified: true}
  const receipt = {...input, suite_key: 'suite-a', revision: 3, published_by: null, published_at: ''}
  assert.equal(checkedAdoption(receipt, 'suite-a', input), receipt)
  for (const changes of [{operation_id: 'other'}, {suite_key: 'other'}, {revision_id: 'other'}, {force_unverified: false}, {revision: 4}]) assert.throws(() => checkedAdoption({...receipt, ...changes}, 'suite-a', input), /发布结果与本次操作不一致/)
})
test('a lost publish response is not retried by the adapter', async () => {
  const original = globalThis.fetch, calls: string[] = []
  globalThis.fetch = async (url, init) => {calls.push(`${init?.method} ${String(url)}`); throw new Error('reply lost')}
  try {await assert.rejects(nativeCheckerReleaseAdapter.adopt('suite/a', {revision_id: 'r', expected_revision: 0, operation_id: 'op', force_unverified: false}), /reply lost/); assert.equal(calls.length, 1); assert.match(calls[0], /suites\/suite%2Fa\/active$/)} finally {globalThis.fetch = original}
})
