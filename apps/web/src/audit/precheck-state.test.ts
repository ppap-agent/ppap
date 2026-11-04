import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {checkerLabel, precheckFacts, precheckFiles, precheckIsCurrent, preparationAction, type PrecheckReceipt} from './precheck-state.ts'

type File = components['schemas']['AuditTaskFile']
const file: File = {file: {id: 'v1', file_id: 'f1', original_filename: '原件.xlsx', content_type: 'application/octet-stream', byte_size: 1, sha256: null, status: 'ready'}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: {state: 'matched', rule: {id: 'rule', title: '适用规则', body: {kind: 'engine', rule_profile: 'v4', type: '控制计划', customers: ['蔚来'], models: [], parts: [], rules: []}}, document_type: '控制计划', gaps: [], metadata: {customer: null, model: null, part: null, part_number: null}}}
const snapshot = (result: unknown): PrecheckReceipt => ({id: 's1', task_id: 't1', input_revision: 2, current: true, status: 'succeeded', input: {files: [{file: file.file}]}, result: result as PrecheckReceipt['result'], created_at: 'now', updated_at: 'now'})

test('precheck parser tolerates malformed observations without inventing a pass', () => {
  assert.deepEqual(precheckFacts(snapshot({files: {}}), 'v1'), [])
  const facts = precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [null, {id: 'S1', status: 'new-provider-value'}]}]}), 'v1')
  assert.equal(facts[0].status, 'unknown')
  assert.equal(facts[1].status, 'new-provider-value')
  assert.deepEqual(precheckFacts(snapshot({files: [{file_version_id: 'another', facts: [{status: 'satisfied'}]}]}), 'v1'), [])
  assert.deepEqual(precheckFiles(snapshot({})), [{version: 'v1', name: '原件.xlsx'}])
})

test('warnings, uncovered checks and pure execution unknowns retain actionable readiness', () => {
  const observations = [
    {status: 'unknown'}, {status: 'not_checked'}, {status: 'error'}, {status: 'handoff'}, {status: 'satisfied'},
    {status: 'not_checked', code: 'S01'}, {status: 'not_checked', code: 'S02'},
    {status: 'defect', code: 'W02', certainty: 'confirmed'}, {status: 'defect', code: 'W03', certainty: 'confirmed'},
  ]
  for (const fact of observations) {
    const facts = precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [fact]}]}), 'v1')
    assert.equal(preparationAction(file, facts), 'ready', JSON.stringify(fact))
  }
  assert.equal(preparationAction({...file, validation: {state: 'error', reasons: ['technical failure']}}, []), 'confirm')
  assert.equal(preparationAction({...file, validation: {state: 'invalid', reasons: ['damaged']}}, []), 'process')
})

test('only confirmed critical structural defects with original evidence require processing', () => {
  const observed = {id: 'S1', status: 'defect', code: 'B05', certainty: 'confirmed', critical: true, certified: false, evidence: {sources: [{locator: JSON.stringify({file_version_id: 'v1', sheet: '主表', cell: 'A1'}), excerpt: '缺少结构列'}]}}
  const action = (fact: object) => preparationAction(file, precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [fact]}]}), 'v1'))
  assert.equal(action(observed), 'process')
  for (const change of [{critical: false}, {certainty: 'unknown'}, {evidence: null}, {code: 'B99'}, {critical: 'true'}, {code: 'W02'}, {code: 'W03'}]) assert.equal(action({...observed, ...change}), 'ready')
  assert.equal(action({...observed, evidence: {sources: [{locator: JSON.stringify({file_version_id: 'another', cell: 'A1'}), excerpt: 'Not this original'}]}}), 'ready')
  assert.equal(action({status: 'unknown', code: 'B03'}), 'confirm')
  assert.equal(action({status: 'defect', code: 'B03'}), 'confirm')
})

test('routing uncertainty and a known rule gap have distinct recovery groups', () => {
  assert.equal(preparationAction({...file, match: {...file.match!, state: 'unmatched', gaps: ['matching_customer_required']}}, []), 'confirm')
  assert.equal(preparationAction({...file, match: {...file.match!, state: 'unmatched', gaps: ['matching_document_type_rules_missing']}}, []), 'process')
  assert.equal(preparationAction({...file, match: {...file.match!, state: 'unmatched', document_type: null, gaps: ['matching_document_type_rules_missing']}}, []), 'confirm')
  assert.equal(preparationAction({...file, match: {...file.match!, state: 'unmatched', gaps: ['matching_customer_required', 'matching_document_type_rules_missing']}}, []), 'confirm')
  assert.match(checkerLabel({...file, match: {...file.match!, state: 'reference', rule: null}}), /不单独审核或收费/)
})

test('old revisions and superseded attempts never describe current inputs', () => {
  const current = snapshot({})
  assert.equal(precheckIsCurrent(current, {id: 't1', input_revision: 2}), true)
  assert.equal(precheckIsCurrent(current, {id: 't1', input_revision: 3}), false)
  assert.equal(precheckIsCurrent({...current, current: false}, {id: 't1', input_revision: 2}), false)
  assert.equal(precheckIsCurrent(current, {id: 'other', input_revision: 2}), false)
})


test('an unimplemented or unfamiliar check is retained as a detail without inventing a confirmation task', () => {
  for (const code of ['S01', 'S02', '', 'S03', 'unrecognized']) {
    const facts = precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [{status: 'not_checked', code}]}]}), 'v1')
    assert.equal(preparationAction(file, facts), 'ready')
    assert.equal(facts[0].status, 'not_checked')
  }
})
