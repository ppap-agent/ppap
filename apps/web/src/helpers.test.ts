import assert from 'node:assert/strict'
import test from 'node:test'
import {analysisNotice} from './helpers.ts'

test('recognition notices hide internal excerpt details without hiding actionable failures', () => {
  assert.equal(analysisNotice('metadata_uses_excerpt'), null)
  const fallback = analysisNotice('future_notice_code')
  assert.ok(fallback)
  for (const code of ['ambiguous_available_rules', 'matching_result_unknown', 'matching_document_type_rules_missing', 'matching_customer_rules_missing', 'matching_rules_customer_unassigned', 'matching_document_type_required']) {
    const notice = analysisNotice(code)
    assert.ok(notice && notice !== fallback && !notice.includes(code))
  }
  for (const code of ['MATCH_TIMEOUT', 'matching-timeout', 'matching.timeout']) assert.equal(analysisNotice(code), fallback)
  // A server notice already written for people is shown as written.
  assert.equal(analysisNotice('未选择控制计划，频次一致性缺少跨文件比对资料。'), '未选择控制计划，频次一致性缺少跨文件比对资料。')
})
