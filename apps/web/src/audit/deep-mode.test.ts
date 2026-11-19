import assert from 'node:assert/strict'
import test from 'node:test'

import {billableFileCount, deepModeControl, deepModeDescription, deepReviewNotice, pointsBasis} from './deep-mode.ts'

const file = (id: string, state: 'matched' | 'unmatched' | 'reference') => ({file: {id}, selection: 'selected', match: {state}}) as never
const scope = {target_file_version_ids: ['a', 'b'], reference_file_version_ids: [], excluded_files: []}

test('the points basis is 500 per file in every mode, with no multiplier', () => {
  const standard = pointsBasis({confirmation_scope: scope, files: [], deep_mode: false} as never)
  const deep = pointsBasis({confirmation_scope: scope, files: [], deep_mode: true} as never)
  assert.equal(deep, standard)
  assert.match(deep, /500 点\/份 × 2 份/)
  assert.doesNotMatch(deep, /倍|1\.5|深度/)
  assert.doesNotMatch(deepModeDescription, /倍计|点数|1\.5/)
  assert.match(deepModeDescription, /规则范围与标准审核相同/)
})

test('without a confirmation scope only matched selected files are counted', () => {
  assert.equal(billableFileCount({confirmation_scope: null, files: [file('a', 'matched'), file('b', 'unmatched'), file('c', 'reference')]}), 1)
})

test('the switch is disabled with the server reason when deep mode is unavailable', () => {
  const task = {state: 'pending_confirm', round_id: null, deep_mode: false, deep_mode_option: {available: false, reason: '当前企业没有可用于深度审核的模型。'}} as const
  assert.deepEqual(deepModeControl(task, false), {disabled: true, reason: '当前企业没有可用于深度审核的模型。'})
  // A task already in deep mode can always be switched back.
  assert.deepEqual(deepModeControl({...task, deep_mode: true}, false), {disabled: false, reason: null})
  assert.deepEqual(deepModeControl({...task, deep_mode_option: {available: true, reason: null}}, true), {disabled: true, reason: null})
  assert.deepEqual(deepModeControl({...task, deep_mode_option: {available: true, reason: null}, round_id: 'round-1'}, false), {disabled: true, reason: null})
})

test('only a degraded file says its deep review did not complete', () => {
  assert.ok(deepReviewNotice({state: 'degraded', reason: 'deep_timeout'}))
  assert.equal(deepReviewNotice({state: 'completed'}), null)
  assert.equal(deepReviewNotice(null), null)
})
