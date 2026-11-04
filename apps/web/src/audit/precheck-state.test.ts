import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {checkerLabel, precheckFacts, precheckFiles, precheckIsCurrent, preparationAction, type PrecheckReceipt} from './precheck-state.ts'

type File = components['schemas']['AuditTaskFile']
const file: File = {file: {id: 'v1', file_id: 'f1', original_filename: '原件.xlsx', content_type: 'application/octet-stream', byte_size: 1, sha256: null, status: 'ready'}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: {state: 'reference', rule: null, document_type: null, gaps: [], metadata: {customer: null, model: null, part: null, part_number: null}}}
const snapshot = (result: unknown): PrecheckReceipt => ({id: 's1', task_id: 't1', input_revision: 2, current: true, status: 'succeeded', input: {files: [{file: file.file}]}, result: result as PrecheckReceipt['result'], created_at: 'now', updated_at: 'now'})

test('precheck parser tolerates malformed observations without inventing a pass', () => {
  assert.deepEqual(precheckFacts(snapshot({files: {}}), 'v1'), [])
  const facts = precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [null, {id: 'S1', status: 'new-provider-value'}]}]}), 'v1')
  assert.equal(facts[0].status, 'unknown')
  assert.equal(facts[1].status, 'new-provider-value')
  assert.deepEqual(precheckFacts(snapshot({files: [{file_version_id: 'another', facts: [{status: 'satisfied'}]}]}), 'v1'), [])
  assert.deepEqual(precheckFiles(snapshot({})), [{version: 'v1', name: '原件.xlsx'}])
})

test('unknown and uncovered checks need confirmation without becoming supplier defects', () => {
  for (const status of ['unknown', 'not_checked', 'error']) {
    const facts = precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [{status}]}]}), 'v1')
    assert.equal(preparationAction(file, facts), 'confirm')
  }
  for (const status of ['satisfied', 'handoff']) assert.equal(preparationAction(file, precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [{status}]}]}), 'v1')), 'ready')
  const facts = precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [{status: 'defect'}]}]}), 'v1')
  assert.equal(preparationAction(file, facts), 'process')
  assert.equal(preparationAction({...file, validation: {state: 'error', reasons: ['technical failure']}}, []), 'confirm')
  assert.equal(preparationAction({...file, validation: {state: 'invalid', reasons: ['damaged']}}, []), 'process')
})

test('routing uncertainty and a known rule gap have distinct recovery groups', () => {
  assert.equal(preparationAction({...file, match: {...file.match!, state: 'unmatched', gaps: ['matching_customer_required']}}, []), 'confirm')
  assert.equal(preparationAction({...file, match: {...file.match!, state: 'unmatched', gaps: ['matching_document_type_rules_missing']}}, []), 'process')
  assert.match(checkerLabel(file), /不单独审核或收费/)
})

test('old revisions and superseded attempts never describe current inputs', () => {
  const current = snapshot({})
  assert.equal(precheckIsCurrent(current, {id: 't1', input_revision: 2}), true)
  assert.equal(precheckIsCurrent(current, {id: 't1', input_revision: 3}), false)
  assert.equal(precheckIsCurrent({...current, current: false}, {id: 't1', input_revision: 2}), false)
  assert.equal(precheckIsCurrent(current, {id: 'other', input_revision: 2}), false)
})


test('explicit scope exclusions continue while missing implementation and unknown codes need confirmation', () => {
  for (const code of ['S01', 'S02', '', 'S03', 'unrecognized']) {
    const facts = precheckFacts(snapshot({files: [{file_version_id: 'v1', facts: [{status: 'not_checked', code}]}]}), 'v1')
    assert.equal(preparationAction(file, facts), ['S01', 'S02'].includes(code) ? 'ready' : 'confirm')
  }
})
