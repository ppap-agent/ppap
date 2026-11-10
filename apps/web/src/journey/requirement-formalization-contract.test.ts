import assert from 'node:assert/strict'
import test from 'node:test'
import {canRequestFormalization, checkerAvailability, checkerAvailabilityCopy, confirmedReadingAside, formalizationFailure, formalizationReceiptMatches, sameFormalizationTarget, textForCandidate, visibleFormalizationReadings} from './requirement-formalization-contract.ts'

const view = {ruleVersionId: 'rule-1', clauseId: 'c1'}

test('a changed source keeps the old reading visible but not confirmable', () => {
  const older = {cnl_text: '至少一条'}
  const latest = {cnl_text: '恰好一条'}
  const current = visibleFormalizationReadings('candidate', [older, latest])
  assert.equal(current.current, latest)
  assert.equal(current.previous, older)
  assert.equal(current.historical, null)
  const stale = visibleFormalizationReadings('source_changed', [older, latest])
  assert.equal(stale.current, null)
  assert.equal(stale.historical, latest)
})

test('a stopped explanation can be requested again after a candidate exists', () => {
  assert.equal(canRequestFormalization('not_started'), true)
  assert.equal(canRequestFormalization('not_started', 'unknown'), false)
  assert.equal(canRequestFormalization('not_started', 'queued'), false)
  assert.equal(canRequestFormalization('source_changed'), true)
  assert.equal(canRequestFormalization('candidate', 'stopped'), true)
  assert.equal(canRequestFormalization('candidate', 'failed'), true)
  assert.equal(canRequestFormalization('confirmed', 'stopped'), true)
  assert.equal(canRequestFormalization('candidate', 'completed'), false)
  assert.equal(canRequestFormalization('needs_clarification', 'unknown'), false)
  assert.equal(formalizationFailure('model_unavailable'), '个人模型还不可用。请先配置模型，然后再解释。')
  assert.equal(formalizationFailure('model_consumption_refused'), '你还没有同意使用个人模型。同意后再解释这一条款。')
  assert.equal(formalizationFailure('source_unchecked'), '原文出处未核对。请先核对出处，再确认这一理解。')
  assert.equal(formalizationFailure('formalization_failed'), '这次解释没有完成。可以重新让系统解释。')
  assert.equal(formalizationFailure('source_changed'), '这次解释没有完成。可以重新让系统解释。')
})

test('an older confirmation stays readable when a newer candidate is showing', () => {
  const older = {id: 'old', cnl_text: '至少关联一条'}
  const latest = {id: 'new', cnl_text: '恰好关联一条'}
  assert.equal(confirmedReadingAside('old', [older, latest], [latest])?.cnl_text, '至少关联一条')
  assert.equal(confirmedReadingAside('new', [older, latest], [latest]), null)
  assert.equal(confirmedReadingAside('old', [older, latest], [{id: 'other', cnl_text: '至少关联一条'}]), null)
  assert.equal(confirmedReadingAside('missing', [latest], []), null)
})

test('a clarification choice does not carry to the next candidate', () => {
  assert.equal(textForCandidate('cand-1', 'cand-1', 'exact'), 'exact')
  assert.equal(textForCandidate('cand-2', 'cand-1', 'exact'), '')
  assert.equal(textForCandidate('cand-2', 'cand-1', '改成至少一条'), '')
  assert.equal(textForCandidate('cand-1', 'cand-1', '改成至少一条'), '改成至少一条')
  assert.equal(textForCandidate(undefined, 'cand-1', 'exact'), '')
})

test('a handoff result stays on the rule version and clause that started it', () => {
  const origin = {ruleVersionId: 'rule-1', clauseId: 'c1'}
  assert.equal(sameFormalizationTarget(origin, origin), true)
  assert.equal(sameFormalizationTarget(origin, {ruleVersionId: 'rule-2', clauseId: 'c1'}), false)
  assert.equal(sameFormalizationTarget(origin, {ruleVersionId: 'rule-1', clauseId: 'c2'}), false)
})

test('a receipt applies only to the same operation and clause', () => {
  const receipt = {operation_id: 'op-1', rule_version_id: 'rule-1', clause_id: 'c1'}
  assert.equal(formalizationReceiptMatches(view, receipt, 'op-1'), true)
  assert.equal(formalizationReceiptMatches(view, receipt, 'op-2'), false)
  assert.equal(formalizationReceiptMatches(view, {...receipt, clause_id: 'c2'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(view, {...receipt, rule_version_id: 'rule-2'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(view, {operation_id: 'op-1'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(view, {...receipt, candidate_id: null}, 'op-1'), true)
  const pinned = {...view, candidateId: 'cand-1'}
  assert.equal(formalizationReceiptMatches(pinned, {...receipt, candidate_id: 'cand-1'}, 'op-1'), true)
  assert.equal(formalizationReceiptMatches(pinned, {...receipt, candidate_id: 'cand-2'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(pinned, {...receipt, candidate_id: null}, 'op-1'), false)
})


test('a verified checker is not described as still awaiting verification', () => {
  const verified = {verified: true, samples_not_rerun: false}
  assert.equal(checkerAvailability(verified), 'verified')
  assert.equal(checkerAvailability({verified: true, samples_not_rerun: true}), 'unverified')
  assert.equal(checkerAvailability({verified: false, samples_not_rerun: false}), 'unverified')
  assert.equal(checkerAvailability({verified: true}), 'unverified')
  assert.equal(checkerAvailability(null), 'unknown')
  assert.equal(checkerAvailabilityCopy('verified'), '检查功能已验证，可用于审核。')
  assert.equal(checkerAvailabilityCopy('unverified'), '检查功能仍需单独验证后才能用于审核。')
  assert.equal(checkerAvailabilityCopy('verified').includes('仍需单独验证'), false)
  assert.equal(checkerAvailabilityCopy('unverified').includes('已验证，可用于审核'), false)
})
