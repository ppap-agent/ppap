import assert from 'node:assert/strict'
import test from 'node:test'
import {checkedFeedback, checkedFeedbackView, checkedOptimization, nativeCheckerFeedbackAdapter, optimizationSummary, type CheckerFeedbackInput, type CheckerOptimization, checkerReason, feedbackEvidence, manualEvidence, usageText, checkedFeedbackEdit, checkedFeedbackDeletion, feedbackSourceStatus, latestOptimization} from './checker-feedback-contract.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const input: CheckerFeedbackInput = {operation_id: 'op-a', file_version_id: 'file-a', rule_version_id: 'rule-a', rule_clause_id: 'U08-1', original_status: 'PASS', result_attempt: 1, expected_status: 'NG', rationale: '原件缺少必填内容', evidence: [{file_version_id: 'file-a', locator: {kind: 'xlsx_cell', sheet: 'Sheet1', cell: 'B2'}, excerpt: '原文'}]}
const operation: CheckerOptimization = {operation_id: 'opt-a', suite_revision_id: 'suite-a', suite_key: 'control-plan', state: 'unknown', candidate_revision_id: null, report: null, usage: {source: 'unknown'}, failure: null, cancel_requested: false, created_at: ''}
test('feedback receipts preserve exact frozen result and independently entered answer', () => {
  const saved = {...input, id: 'feedback-a', suite_revision_id: 'suite-a', revision: 1, round_id: 'round-a', suite_key: 'control-plan', ordinal: 1}
  assert.equal(checkedFeedback(saved, input), saved)
  for (const mutation of [{operation_id: 'other'}, {file_version_id: 'other'}, {rule_version_id: 'other'}, {rule_clause_id: 'other'}, {expected_status: 'PASS' as const}, {rationale: '模型建议'}, {evidence: []}]) assert.throws(() => checkedFeedback({...saved, ...mutation}, input))
  assert.throws(() => checkedFeedbackView({round_id: 'other', feedback: [], optimizations: []}, 'round-a'))
})
test('unknown consumption stays unknown and cannot become a fabricated result', () => {
  assert.equal(checkedOptimization(operation, 'opt-a'), operation)
  assert.equal(operation.usage.source, 'unknown')
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

test('manual evidence allows absent machine locators and strips machine-only fields', () => {
  assert.equal(manualEvidence('file-a', 'xlsx_cell', '', 'A1', ''), null)
  assert.equal(manualEvidence('file-a', 'pdf_page', '', '0', ''), null)
  assert.equal(manualEvidence('file-a', 'docx_paragraph', '', '1.2', ''), null)
  const own = manualEvidence('file-a', 'xlsx_cell', ' Sheet1 ', 'b12', '事实')!
  assert.deepEqual(own, {file_version_id: 'file-a', locator: {kind: 'xlsx_cell', sheet: 'Sheet1', cell: 'B12'}, excerpt: '事实'})
  assert.deepEqual(feedbackEvidence([{...own, rendition: 'derived'} as typeof own, {...own, locator: null}]), [own])
})
test('sample mutations validate identity, CAS and answer before reporting success', () => {
  const input = {expected_revision: 1, expected_status: 'NG' as const, rationale: '原件依据', evidence: [manualEvidence('file-a', 'pdf_page', '', '2', '原文')!]}
  const saved = {...input, id: 'sample-a', round_id: 'round-a', revision: 2} as Parameters<typeof checkedFeedbackEdit>[0]
  assert.equal(checkedFeedbackEdit(saved, 'round-a', 'sample-a', input), saved)
  assert.throws(() => checkedFeedbackEdit({...saved, revision: 3}, 'round-a', 'sample-a', input))
  assert.throws(() => checkedFeedbackEdit({...saved, evidence: []}, 'round-a', 'sample-a', input))
  assert.throws(() => checkedFeedbackDeletion({id: 'sample-a', deleted: false, revision: 2}, 'sample-a', 1))
  assert.deepEqual(checkedFeedbackDeletion({id: 'sample-a', deleted: true, revision: 2}, 'sample-a', 1), {id: 'sample-a', deleted: true, revision: 2})
})
test('unknown, controlled and provider usage remain distinct', () => {
  assert.match(usageText({total_tokens: 0, uncertain: true}), /未知/)
  assert.match(usageText({total_tokens: 0, source: 'unknown'}), /未知/)
  assert.match(usageText({input_tokens: 12, output_tokens: 3, source: 'provider'}), /15 Token/)
  assert.match(usageText({total_tokens: 0, source: 'controlled'}), /受控测试/)
  assert.match(checkerReason('oracle_changed'), /正确答案/)
  assert.equal(feedbackSourceStatus('PASS', null), false)
  assert.equal(feedbackSourceStatus('uncovered', 1), false)
  assert.equal(feedbackSourceStatus('UNABLE', 1), true)
})
test('a technical case failure without clauses is never counted as zero failures', () => {
  assert.equal(optimizationSummary({verified: false, reasons: [], total_samples: 1, passed_samples: 0, new_errors: 0, cases: [{case_id: 'a', split: 'holdout', passed: false, error_code: 'worker_failed', clauses: []}]}).errors, 1)
})

test('another entry restores the latest operation for this check even after candidate revision changes', () => {
  const newer = {...operation, operation_id: 'new-candidate-test', suite_revision_id: 'candidate-revision'}
  const other = {...operation, operation_id: 'other-check', suite_key: 'pfmea'}
  const view = {round_id: 'round-a', feedback: [], optimizations: [other, newer, operation]}
  assert.equal(latestOptimization(view, 'control-plan'), newer)
  assert.equal(latestOptimization(view, 'missing'), null)
})

test('explicit grouped selection can be split while independent answers stay intact', async () => {
  const {selectedFeedback} = await import('./checker-feedback-contract.ts')
  const first = {...input, id: 'sample-a', suite_revision_id: 'suite-a', revision: 1, round_id: 'round-a', suite_key: 'control-plan', ordinal: 1}
  const second = {...first, id: 'sample-b', ordinal: 2, expected_status: 'PASS' as const}
  const batch = [first, second]
  assert.deepEqual(selectedFeedback(batch, null, ['sample-a']).map(row => row.id), ['sample-a'])
  assert.deepEqual(selectedFeedback(batch, ['sample-a', 'sample-b'], []).map(row => row.expected_status), ['NG', 'PASS'])
  assert.deepEqual(selectedFeedback(batch, ['sample-b'], []).map(row => row.id), ['sample-b'])
})
