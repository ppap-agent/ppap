import assert from 'node:assert/strict'
import test from 'node:test'
import type {SemanticSource} from './api.ts'
import {analysisNotice, sourceLocation} from './helpers.ts'

test('recognition notices hide internal excerpt details without hiding actionable failures', () => {
  assert.equal(analysisNotice('metadata_uses_excerpt'), null)
  const fallback = analysisNotice('future_notice_code')
  assert.ok(fallback)
  for (const code of ['ambiguous_available_rules', 'matching_result_unknown', 'matching_customer_required', 'matching_document_type_rules_missing', 'matching_customer_rules_missing', 'matching_rules_customer_unassigned', 'matching_document_type_required']) {
    const notice = analysisNotice(code)
    assert.ok(notice && notice !== fallback && !notice.includes(code))
  }
  for (const code of ['MATCH_TIMEOUT', 'matching-timeout', 'matching.timeout']) assert.equal(analysisNotice(code), fallback)
  // A server notice already written for people is shown as written.
  assert.equal(analysisNotice('未选择控制计划，频次一致性缺少跨文件比对资料。'), '未选择控制计划，频次一致性缺少跨文件比对资料。')
})

test('sourceLocation formats each semantic locator kind', () => {
  const file = {file_version_id: 'v', filename: 'a.xlsx', role: 'target' as const}
  const cell: SemanticSource = {...file, locator: {kind: 'xlsx_cell', sheet: '控制计划', cell: 'B12'}, excerpt: 'x'}
  const range: SemanticSource = {...file, locator: {kind: 'xlsx_range', sheet: '控制计划', start: 'A1', end: 'C3'}, excerpt: 'x'}
  const pdf: SemanticSource = {...file, filename: 'a.pdf', locator: {kind: 'pdf_block', page: 2, block: 4}, excerpt: 'x'}
  const pdfCell: SemanticSource = {...file, filename: 'a.pdf', locator: {kind: 'pdf_cell', page: 3, row: 12, column: 4}, excerpt: 'x'}
  const paragraph: SemanticSource = {...file, filename: 'a.docx', locator: {kind: 'docx_paragraph', paragraph: 7}, excerpt: 'x'}
  const table: SemanticSource = {...file, filename: 'a.docx', locator: {kind: 'docx_cell', table: 1, row: 3, column: 2}, excerpt: 'x'}
  assert.equal(sourceLocation(cell), '控制计划!B12')
  assert.equal(sourceLocation(range), '控制计划!A1:C3')
  assert.equal(sourceLocation(pdf), '第 2 页 · 文本块 4')
  assert.equal(sourceLocation(pdfCell), '第 3 页 · 表格行 12 · 列 4')
  const pdfPage: SemanticSource = {...file, filename: 'a.pdf', locator: {kind: 'pdf_page', page: 4, region: [null, 10.5, null, 80.25]}, excerpt: 'x'}
  assert.equal(sourceLocation(pdfPage), '第 4 页 · 区域 未定, 10.5, 未定, 80.25')
  assert.equal(sourceLocation(paragraph), '段落 7')
  assert.equal(sourceLocation(table), '表格 1 · 行 3 · 列 2')
})
