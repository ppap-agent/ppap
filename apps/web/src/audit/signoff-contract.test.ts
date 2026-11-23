import assert from 'node:assert/strict'
import test from 'node:test'
import {factLockOf, fileConclusion, reviewLockOf, signoffEventText, signoffStatusText, signoffSummary, type SignoffContent} from './signoff-contract.ts'

const file = (filename: string, conclusion: SignoffContent['files'][number]['conclusion'], counts: {reviewed: number; accepted: number; pending: number}) => ({
  file_version_id: filename, filename, rule_title: null, conclusion, machine_conclusion: conclusion, pending_count: counts.pending, reviewed_count: counts.reviewed, accepted_count: counts.accepted, report: null,
})

test('the status says who signed and when in Beijing time, or that the round is not signed', () => {
  assert.equal(signoffStatusText({state: 'signed', signer: {name: '张三', mine: true}, signed_at: '2026-11-22T06:32:05+00:00'}), '已签发 · 张三 · 2026-11-22 14:32')
  assert.equal(signoffStatusText({state: 'unsigned', signer: null, signed_at: null}), '未签发')
})

test('the confirmation says how many files and how their items were settled; pending items are counted apart, never passed', () => {
  const content: SignoffContent = {
    conclusion: 'UNKNOWN', machine_conclusion: 'UNKNOWN', reviewed_count: 3, accepted_count: 40, reviews: [], unreviewed: [], report_fingerprint: 'r', fingerprint: 'f',
    files: [file('PFMEA.xlsx', 'PASS', {reviewed: 2, accepted: 18, pending: 0}), file('PSW.pdf', 'UNKNOWN', {reviewed: 1, accepted: 22, pending: 4})],
  }
  assert.deepEqual(signoffSummary(content), {conclusion: 'UNKNOWN', line: '2 份文件 · 人工确认 3 项 · 按机器结论接受 40 项', pending: 4})
  assert.equal(signoffSummary({...content, conclusion: null, machine_conclusion: 'NG'}).conclusion, 'NG')
})

test('a file without a conclusion as shown falls back to the machine one, and without either is 待审核', () => {
  assert.equal(fileConclusion({conclusion: null, machine_conclusion: 'NG'}), 'NG')
  assert.equal(fileConclusion({conclusion: null, machine_conclusion: null}), 'UNKNOWN')
})

test('the record names the action, who, when and a withdrawal reason', () => {
  assert.deepEqual(signoffEventText({action: 'withdrawn', by: '张三', at: '2026-11-22T07:00:00+00:00', reason: '复核漏了一项'}), {action: '撤回签发', who: '张三', when: '2026-11-22 15:00', reason: '复核漏了一项'})
  assert.deepEqual(signoffEventText({action: 'signed', by: '', at: '2026-11-22T06:00:00+00:00', reason: null}), {action: '签发', who: '签发人', when: '2026-11-22 14:00', reason: ''})
})

test('reviews close only while the round is signed', () => {
  assert.equal(reviewLockOf({state: 'signed'}), '撤回签发后才能更正复核')
  assert.equal(reviewLockOf({state: 'unsigned'}), undefined)
  assert.equal(reviewLockOf(null), undefined)
})

test('a signed round closes confirming original facts as it closes reviews, until the signoff is withdrawn', () => {
  assert.equal(factLockOf({state: 'signed'}), '这一轮已签发，撤回签发后才能确认原件事实')
  assert.equal(factLockOf({state: 'unsigned'}), undefined)
  assert.equal(factLockOf(null), undefined)
})
