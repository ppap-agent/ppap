import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {checkerLabel, hasTaskPrecheck, precheckFacts, precheckFiles, precheckIsCurrent, preparationAction, type PrecheckReceipt} from './precheck-state.ts'

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

test('precheck visibility comes from server Suite identity or a persisted receipt, not profile labels', () => {
  assert.equal(hasTaskPrecheck({files: [file]}), false)
  for (const profile of ['v2', 'v3', 'v4', undefined]) {
    const matched = {...file, match: {...file.match!, rule: {...file.match!.rule!, body: {...file.match!.rule!.body, rule_profile: profile, suite_key: 'control-plan'}}}} as File
    assert.equal(hasTaskPrecheck({files: [matched]}), true)
    assert.equal(hasTaskPrecheck({files: [{...matched, selection: 'removed'}]}), false)
    assert.equal(hasTaskPrecheck({files: [{...matched, match: {...matched.match!, state: 'reference'}}]}), false)
    assert.doesNotMatch(checkerLabel(matched), /V[234]/)
    assert.match(checkerLabel(matched), /本企业采用版本/)
  }
  assert.equal(hasTaskPrecheck({files: [], precheck: snapshot({})}), true)
})
test('checker labels use exact frozen Suite facts and do not invent a generic fallback', () => {
  assert.match(checkerLabel(file, snapshot({files: [{file_version_id: 'v1', checker: {profile: 'v2'}}]})), /来源尚未确认/)
  const receipt = {...snapshot({}), input: {files: [{file: file.file, rule_execution: {mode: 'checker_core'}, suite_target: {suite_revision_id: 'revision-a', suite_key: 'control-plan', rules: {title: '控制计划检查'}, verification: 'unverified'}}]}}
  assert.equal(checkerLabel(file, receipt), '控制计划检查 · 本次采用的检查 · 需要重新验证')
  assert.doesNotMatch(checkerLabel({...file, file: {...file.file, id: 'other'}}, receipt), /本次采用的检查/)
  assert.match(checkerLabel(file, {...receipt, input: {files: [{file: file.file, rule_execution: {mode: 'unavailable'}}]}}), /尚无可运行的检查，未检查/)
  const carrier = {...file, match: {...file.match!, gaps: ['suite_version_confirmation_required']}}
  assert.match(checkerLabel(carrier, {...receipt, input: {files: [{file: file.file, rule_execution: {mode: 'unavailable', reason: 'suite_version_confirmation_required'}}]}}), /需确认目录版本后才能检查/)
  assert.doesNotMatch(checkerLabel(carrier), /已检查/)
})


test('Suite preparation presents the server admission decision and actionable library or risk disclosure', () => {
  const admission = {state: 'validation_required', suite_key: 'suite-a', suite_revision_id: 'revision-a', verification_status: 'stale', force_unverified: false, samples_not_rerun: false} as const
  const blocked = {...file, suite_admission: admission}
  assert.equal(preparationAction(blocked, []), 'confirm')
  assert.match(checkerLabel(blocked), /需要重新验证，暂不能开始审核/)
  const forced = {...file, suite_admission: {...admission, state: 'ready' as const, force_unverified: true}}
  assert.equal(preparationAction(forced, []), 'ready')
  assert.match(checkerLabel(forced), /已明确采用未验证版本/)
  const library = {...file, suite_admission: {...admission, state: 'ready' as const, verification_status: 'verified' as const, samples_not_rerun: true}}
  assert.equal(preparationAction(library, []), 'ready')
  assert.match(checkerLabel(library), /官方已更新，尚未重新验证/)
  const combined = checkerLabel({...forced, suite_admission: {...forced.suite_admission, samples_not_rerun: true}, match: {...forced.match!, gaps: ['published_rules_changed']}})
  for (const notice of ['已明确采用未验证版本', '官方已更新，尚未重新验证', '新规则尚未采用']) assert.ok(combined.includes(notice))
})
