import assert from 'node:assert/strict'
import test from 'node:test'
import {factMachineFindings, factValueLabel, groupFactQuestions, nativeFactAnswersAdapter, type FactAnswerReceipt, type FactAnswerRequest, type FactQuestion} from './fact-answers-contract.ts'
import type {components} from '../generated/openapi.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})

const question = (field: string, items: string[], file = 'file-1', answered = false): FactQuestion => ({file_version_id: file, field, prompt: field, answer_type: 'boolean', locator: {kind: 'pdf_page', page: 1}, clause_ids: items, item_keys: items, fingerprint: 'a'.repeat(64), answered})

test('human confirmation resolves the original machine result by its canonical review key', () => {
  const finding = {item_key: 'R1', item: 'SOP阶段', status: 'UNABLE'}
  const checks = [{review_item_key: 'original-version:R1', finding}] as components['schemas']['AuditClauseEvaluation'][]
  assert.deepEqual(factMachineFindings(checks), [{...finding, item_key: 'original-version:R1'}])
  assert.equal(finding.item_key, 'R1')
})

test('questions group by frozen file and surface three with the most dependent clauses', () => {
  const rows = [question('one', ['1']), question('four', ['1', '2', '3', '4']), question('two', ['1', '2']), question('three', ['1', '2', '3']), question('other', ['5'], 'file-2'), question('answered', ['1', '2', '3', '4', '5'], 'file-1', true)]
  const groups = groupFactQuestions(rows)
  assert.deepEqual(groups.map(group => group.fileVersionId), ['file-1', 'file-2'])
  assert.deepEqual(groups[0].visible.map(row => row.field), ['four', 'three', 'two'])
  assert.deepEqual(groups[0].folded.map(row => row.field), ['one'])
  assert.equal(rows[0].field, 'one')
  assert.equal(factValueLabel(null), '看不清')
  assert.equal(factValueLabel(false), '否')
  assert.equal(factValueLabel(true), '是')
  assert.equal(factValueLabel('A-123'), 'A-123')
})

test('explicit human answer uses one POST and its original operation for readback', async () => {
  const originalFetch = globalThis.fetch, calls: Array<{url: string; init?: RequestInit}> = []
  const input: FactAnswerRequest = {operation_id: 'op-1', expected_revision: 0, file_version_id: 'file-1', field: 'stage.checked', expected_question_fingerprint: 'a'.repeat(64), value: true}
  const receipt: FactAnswerReceipt = {operation_id: 'op-1', round_id: 'round/1', revision: 1, model_calls: 0, answer: {operation_id: 'op-1', round_id: 'round/1', file_version_id: 'file-1', field: input.field, value: true, answered_by: 'reviewer', answered_at: '2026-11-06T10:00:00Z', locator: {kind: 'pdf_page', page: 1}, prompt: '是否选中？', answer_type: 'boolean'}, results: []}
  globalThis.fetch = async (url, init) => {calls.push({url: String(url), init}); return new Response(JSON.stringify(receipt), {status: 200})}
  try {
    assert.deepEqual(await nativeFactAnswersAdapter.apply('round/1', input), receipt)
    assert.deepEqual(await nativeFactAnswersAdapter.readOperation('round/1', 'op-1'), receipt)
    assert.deepEqual(calls.map(call => [call.url, call.init?.method]), [['/audit-rounds/round%2F1/fact-answers', 'POST'], ['/audit-rounds/round%2F1/fact-answers/op-1', 'GET']])
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), input)
  } finally {globalThis.fetch = originalFetch}
})

test('explicit fact sample reuses feedback readback without repeating a write', async () => {
  const originalFetch = globalThis.fetch, calls: Array<{url: string; init?: RequestInit}> = []
  const input = {operation_id: 'sample-op', item_key: 'R1', expected_status: 'NG' as const, rationale: '原件未勾选'}
  const sample = {operation_id: 'sample-op', round_id: 'round/1', expected_status: 'NG'}
  globalThis.fetch = async (url, init) => {
    calls.push({url: String(url), init})
    if (init?.method === 'POST') throw new TypeError('Connection lost after write')
    return new Response(JSON.stringify({round_id: 'round/1', feedback: [sample], optimizations: []}), {status: 200})
  }
  try {
    await assert.rejects(nativeFactAnswersAdapter.saveSample!('round/1', 'answer/1', input))
    assert.deepEqual(await nativeFactAnswersAdapter.readSample!('round/1', 'sample-op'), sample)
    assert.deepEqual(calls.map(call => [call.url, call.init?.method]), [['/audit-rounds/round%2F1/fact-answers/answer%2F1/samples', 'POST'], ['/audit-rounds/round%2F1/checker-feedback', 'GET']])
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), input)
  } finally {globalThis.fetch = originalFetch}
})
