import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import type {DraftSaveRequest, Material, Round} from '../api'
import {draftPlan, inputItems, matchedTaskMaterials, sameAcceptedPlan, submitFrozenRound, submitMatchedTaskRound, type RoundCommands} from './frozen-round.ts'

type Task = components['schemas']['AuditTask']
const types = ['PFMEA', '控制计划', '过程流程图'] as const
function task(): Task {
  return {
    id: 'task-1', number: 'PPAP-20261016-001', state: 'pending_confirm', analysis_mode: 'standard',
    created_at: '2026-10-16T00:00:00Z', updated_at: '2026-10-16T00:00:00Z', source_draft_id: 'draft-1',
    round_id: null, customer_preference: null, completion_reason: null, estimated_points: 1500,
    files: types.map((type, i) => {
      const rule: components['schemas']['BoundRule'] = {id: 'rule-' + (i + 1), title: type, body: {kind: 'engine', type, rule_profile: 'v3', customers: [], models: [], parts: [], rules: [{id: '1', category: type, priority: null, rule_to_check: type + '检查条款', type_of_rule: 'table'}]}}
      return {file: {file_id: 'file-' + i, id: 'version-' + i, original_filename: type + '.xlsx', content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', byte_size: 1024, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: {state: 'matched', metadata: {customer: null, model: null, part: null}, document_type: type, rule, gaps: []}}
    }),
  }
}
function round(id: string, items: Material[], mode: Task['analysis_mode'] = 'standard'): Round {
  return {id: 'round-1', source_draft_id: id, analysis_mode: mode, inputs: items} as Round
}
function commands(received: string[], result: (id: string, body: DraftSaveRequest) => Round): RoundCommands {
  let saved: DraftSaveRequest | null = null
  return {saveDraft: async (id, body) => {received.push('save:' + id); saved = structuredClone(body)}, acceptDraft: async (id, key) => {
    received.push('accept:' + id + ':' + key)
    assert.ok(saved, 'save must precede accept')
    return result(id, saved)
  }}
}

test('V3.1 freezes three independently matched file versions as targets', () => {
  const rows = matchedTaskMaterials(task())
  assert.deepEqual(rows.map(row => [row.file_version_id, row.role, row.rule_version_id]), [
    ['version-0', 'target', 'rule-1'], ['version-1', 'target', 'rule-2'], ['version-2', 'target', 'rule-3'],
  ])
  assert.deepEqual(inputItems(rows).map(row => row.rule_version_id), ['rule-1', 'rule-2', 'rule-3'])
})

test('unselected, removed and unmatched files are not implicit reference files', () => {
  const value = task()
  value.files[0].selection = 'unselected'
  value.files[1].selection = 'removed'
  value.files[1].removal_reason = '用户移除'
  assert.deepEqual(matchedTaskMaterials(value).map(row => row.file_version_id), ['version-2'])
  value.files[2].match = {...value.files[2].match!, state: 'unmatched', rule: null}
  assert.throws(() => matchedTaskMaterials(value), /没有已匹配/)
})

test('precheck, rule mismatches, duplicates and unknown acceptance fail before submit', () => {
  const original = task()
  const pending = structuredClone(original)
  pending.files[1].match!.state = 'matching'
  assert.throws(() => matchedTaskMaterials(pending), /尚未完成匹配/)
  const invalid = structuredClone(original)
  invalid.files[1].validation.state = 'invalid'
  assert.throws(() => matchedTaskMaterials(invalid), /预检查和上传/)
  const mismatch = structuredClone(original)
  if (mismatch.files[1].match?.rule?.body.kind === 'engine') mismatch.files[1].match.rule.body.type = 'PFMEA'
  assert.throws(() => matchedTaskMaterials(mismatch), /不一致/)
  const duplicate = structuredClone(original)
  duplicate.files[1].file.id = duplicate.files[0].file.id
  assert.throws(() => matchedTaskMaterials(duplicate), /重复/)
  const unknown = structuredClone(original)
  unknown.acceptance = {request_id: 'lost-reply', state: 'pending', reason: null}
  assert.throws(() => matchedTaskMaterials(unknown), /受理结果/)
})

test('V3.1 and unified journey submit through identical existing Draft/Accept commands', async () => {
  const value = task(), calls: string[] = []
  const accepted = await submitMatchedTaskRound(value, 'request-1', commands(calls, (id, body) => {
    assert.equal(body.analysis_mode, 'standard')
    assert.deepEqual(body.items?.map(row => [row.role, row.rule_version_id]), [['target', 'rule-1'], ['target', 'rule-2'], ['target', 'rule-3']])
    return round(id, matchedTaskMaterials(value))
  }))
  assert.equal(accepted.id, 'round-1')
  assert.deepEqual(calls, ['save:draft-1', 'accept:draft-1:request-1'])
  const oldItems: Material[] = [...matchedTaskMaterials(value).slice(0, 1), {file_id: 'reference', file_version_id: 'reference-v1', original_filename: '资料.pdf', role: 'reference', rule_version_id: null}]
  const oldCalls: string[] = []
  await submitFrozenRound({draftId: 'unified-draft', items: oldItems, analysisMode: 'standard', idempotencyKey: 'unified-key', caseContext: {registration: 'scope'}},
    commands(oldCalls, (id, body) => {
      assert.equal(body.case_context?.registration, 'scope')
      assert.deepEqual(body.items?.map(row => [row.role, row.rule_version_id]), [['target', 'rule-1'], ['reference', null]])
      return round(id, oldItems)
    }))
  assert.deepEqual(oldCalls, ['save:unified-draft', 'accept:unified-draft:unified-key'])
})

test('a stale or mismatched accepted round cannot masquerade as success', async () => {
  const value = task(), items = matchedTaskMaterials(value)
  const frozen = draftPlan({items, analysis_mode: value.analysis_mode})
  assert.equal(sameAcceptedPlan(round('draft-1', items), 'draft-1', frozen), true)
  assert.equal(sameAcceptedPlan(round('another', items), 'draft-1', frozen), false)
  const calls: string[] = []
  await assert.rejects(submitMatchedTaskRound(value, 'request-1', commands(calls, id => {
    const changed = structuredClone(items)
    changed[1].rule_version_id = 'unrelated-rule'
    return round(id, changed)
  })), /冻结的文件及规则不一致/)
  assert.deepEqual(calls, ['save:draft-1', 'accept:draft-1:request-1'])
})

test('without a real server draft V3.1 must not submit a round', async () => {
  const value = task(), calls: string[] = []
  value.source_draft_id = null
  await assert.rejects(submitMatchedTaskRound(value, 'request-1', commands(calls, id => round(id, []))), /服务端审核草稿/)
  assert.deepEqual(calls, [])
})
