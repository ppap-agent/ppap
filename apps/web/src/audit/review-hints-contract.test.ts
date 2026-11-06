import assert from 'node:assert/strict'
import test from 'node:test'
import {reviewHintText, reviewHintUrl} from './review-hints-contract.ts'
import {filterChecks} from './review-navigation.ts'
import type {components} from '../generated/openapi'

const hint: components['schemas']['ReviewHint'] = {item_key: 'file:rule:R1', reason: 'same_file_changed', compared_round_id: 'previous', compared_file_version_id: 'previous-file', compared_item_key: 'previous-file:rule:R1', clause_id: 'R1', suite_revision_id: 'suite', current_status: 'NG', compared_status: 'PASS', compared_filename: '控制计划.xlsx', result_attempt: 1, compared_result_attempt: 2}
test('the review suggestion names both conclusions and links the exact prior result', () => {
  assert.equal(reviewHintText(hint), '同一份文件、同一检查版本和条款，在不同轮次出现不同结论：符合 → 不符合。')
  const url = new URL(reviewHintUrl(hint), 'https://example.test')
  assert.equal(url.searchParams.get('round'), 'previous')
  assert.equal(url.searchParams.get('file'), 'previous-file')
  assert.equal(url.searchParams.get('clause'), 'R1')
  assert.equal(url.searchParams.get('basis'), '1')
})
test('suggested review is an independent filter and retains the machine result', () => {
  const check: components['schemas']['AuditClauseEvaluation'] = {rule_item: {id: 'R1', category: '条款', rule_to_check: '规则'}, execution: 'completed', coverage: 'executed', evidence_verified: true, evidence: [], scope: null, scope_notice: null, finding: {item_key: hint.item_key, status: 'NG', classification: '依据', item: '规则', suggestions: '', priority: null, locator: null, review: null, review_hint: hint}}
  assert.deepEqual(filterChecks([check], '', 'suggested'), [check])
  assert.deepEqual(filterChecks([check], '', 'ng'), [check])
  assert.match(reviewHintText({...hint, reason: 'cross_format_differs'}), /明确关联的 PDF 与 Excel/)
})
