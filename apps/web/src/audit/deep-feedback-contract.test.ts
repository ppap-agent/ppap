import assert from 'node:assert/strict'
import test from 'node:test'
import {checkedRuleChange, definiteRuleChangeRejection, nativeDeepFeedbackAdapter, type RuleChangeReceipt, type RuleChangeRequest} from './deep-feedback-contract.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})

const receipt: RuleChangeReceipt = {operation_id: 'operation-1', workspace_id: 'workspace-1', state: 'saved', revision: 1, source_round_id: 'round-1', analysis_round_id: 'round-2', analysis_state: 'queued', appendix: []}
const input: RuleChangeRequest = {confirmation_source: 'user', operation_id: 'operation-1', expected_revision: 0, question_id: 'question-1', candidate_id: 'candidate-1', kind: 'mapping', content: '本文件的作业单元编号表示工序编号。', scope: {document_ids: ['file-1'], rule_ids: ['R1']}, intent: 'save_and_analyze'}

test('rule confirmation sends explicit content and scope once and reads a lost reply using the same operation', async () => {
  const previous = globalThis.fetch, calls: Array<{url: string; init?: RequestInit}> = []
  globalThis.fetch = async (url, init) => {
    calls.push({url: String(url), init})
    if (init?.method === 'POST') throw new Error('reply lost')
    return new Response(JSON.stringify(receipt), {status: 200, headers: {'Content-Type': 'application/json'}})
  }
  try {
    await assert.rejects(nativeDeepFeedbackAdapter.apply('round-1', input, 'workspace-1'), /reply lost/)
    const saved = await nativeDeepFeedbackAdapter.readOperation('round-1', input.operation_id, 'workspace-1')
    assert.equal(saved?.analysis_round_id, 'round-2')
    assert.deepEqual(calls.map(call => call.init?.method), ['POST', 'GET'])
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), input)
    assert.equal(calls[1].url, '/audit-rounds/round-1/rule-changes/operation-1')
    assert.equal(calls[0].init?.credentials, 'include')
  } finally {globalThis.fetch = previous}
})

test('foreign operation and workspace receipts cannot become successful saves', () => {
  assert.throws(() => checkedRuleChange({...receipt, operation_id: 'foreign'}, 'workspace-1', 'operation-1'))
  assert.throws(() => checkedRuleChange({...receipt, workspace_id: 'foreign'}, 'workspace-1', 'operation-1'))
  assert.equal(checkedRuleChange({...receipt, source_round_id: 'later-analysis'}, 'workspace-1', 'operation-1').source_round_id, 'later-analysis')
})

test('an absent readback and transport or server failure remain uncertain', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => new Response('null', {status: 200, headers: {'Content-Type': 'application/json'}})
  try {assert.equal(await nativeDeepFeedbackAdapter.readOperation('round-1', 'operation-1', 'workspace-1'), null)} finally {globalThis.fetch = previous}
  for (const failure of [new Error('offline'), {status: 500}, {status: 502}, {status: 409}, {status: 409, code: 'idempotency_conflict'}]) assert.equal(definiteRuleChangeRejection(failure), false)
  for (const status of [400, 401, 403, 404, 422]) assert.equal(definiteRuleChangeRejection({status}), true)
  assert.equal(definiteRuleChangeRejection({status: 409, code: 'feedback_question_changed'}), true)
})
