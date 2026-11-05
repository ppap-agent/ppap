import assert from 'node:assert/strict'
import test from 'node:test'
import {checkedFeedback, checkedFeedbackView, checkedOptimization, nativeCheckerFeedbackAdapter, optimizationSummary, type CheckerFeedbackInput, type CheckerOptimization} from './checker-feedback-contract.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const input: CheckerFeedbackInput = {operation_id: 'op-a', file_version_id: 'file-a', rule_version_id: 'rule-a', rule_clause_id: 'U08-1', original_status: 'PASS', result_attempt: 1, expected_status: 'NG', rationale: '原件缺少必填内容', evidence: [{file_version_id: 'file-a', locator: null, excerpt: '原文'}]}
const operation: CheckerOptimization = {operation_id: 'opt-a', suite_revision_id: 'suite-a', state: 'unknown', candidate_revision_id: null, report: null, usage: null, failure: null}
test('feedback receipts preserve exact frozen result and independently entered answer', () => {
  const saved = {...input, id: 'feedback-a', suite_revision_id: 'suite-a'}
  assert.equal(checkedFeedback(saved, input), saved)
  for (const mutation of [{operation_id: 'other'}, {file_version_id: 'other'}, {rule_version_id: 'other'}, {rule_clause_id: 'other'}, {expected_status: 'PASS' as const}, {rationale: '模型建议'}, {evidence: []}]) assert.throws(() => checkedFeedback({...saved, ...mutation}, input))
  assert.throws(() => checkedFeedbackView({round_id: 'other', feedback: [], optimizations: []}, 'round-a'))
})
test('unknown consumption stays unknown and cannot become a fabricated result', () => {
  assert.equal(checkedOptimization(operation, 'opt-a'), operation)
  assert.equal(operation.usage, null)
  assert.equal(operation.candidate_revision_id, null)
  assert.throws(() => checkedOptimization({...operation, operation_id: 'other'}, 'opt-a'))
  assert.throws(() => checkedOptimization(operation, 'opt-a', 'other'))
})
test('readback after a lost optimization response is GET only with the original identity', async () => {
  const original = globalThis.fetch, calls: string[] = []
  globalThis.fetch = async (url, init) => {
    calls.push(`${init?.method} ${String(url)}`)
    if (init?.method === 'POST') throw new Error('reply lost')
    return new Response(JSON.stringify(operation), {status: 200, headers: {'Content-Type': 'application/json'}})
  }
  try {
    await assert.rejects(nativeCheckerFeedbackAdapter.optimize('suite-a', {operation_id: 'opt-a', feedback_ids: ['feedback-a'], action: 'run_tests', consume_model: true}), /reply lost/)
    assert.equal((await nativeCheckerFeedbackAdapter.operation('opt-a')).state, 'unknown')
    assert.deepEqual(calls, ['POST /suite-revisions/suite-a/optimizations', 'GET /checker-optimizations/opt-a'])
  } finally {globalThis.fetch = original}
})
test('regression summary distinguishes false pass, false failure, unknown and technical failures', () => {
  const row = {clause_id: 'U08-1', expected: 'NG', actual: 'NG', baseline_actual: 'PASS', evidence_locatable: true, regression: false, kind: 'match'}
  const summary = optimizationSummary({verified: false, reasons: [], total_samples: 5, passed_samples: 1, new_errors: 1, cases: [{case_id: 'case-a', split: 'holdout', passed: false, error_code: null, clauses: [row, {...row, kind: 'missed_detection', regression: true}, {...row, kind: 'false_alarm'}, {...row, kind: 'UNABLE'}, {...row, kind: 'ERR'}]}]})
  assert.deepEqual(summary, {fixed: 1, regressions: 1, unknown: 1, errors: 1, falsePass: 1, falseNg: 1})
})
