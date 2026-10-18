import assert from 'node:assert/strict'
import test from 'node:test'
import type {Finding, Rule, SemanticSource} from './api.ts'
import {
  analysisNotice,
  findingShortTitle,
  latestRules,
  matchesFilter,
  parseExcerptCells,
  parseWorkspacePosition,
  sourceLocation,
  workspaceStorageKey,
} from './helpers.ts'

test('recognition notices hide internal excerpt details without hiding actionable failures', () => {
  assert.equal(analysisNotice('metadata_uses_excerpt'), null)
  assert.equal(analysisNotice('ambiguous_available_rules'), '有多份生效规则符合条件，暂时无法确定审核依据。请联系支持核对规则。')
  assert.equal(analysisNotice('matching_result_unknown'), '尚未取得完整匹配结果，请读取识别结果。')
  assert.equal(analysisNotice('future_notice_code'), '识别信息需要核对，请读取识别结果；若仍有异常，请联系支持。')
  for (const code of ['MATCH_TIMEOUT', 'matching-timeout', 'matching.timeout']) assert.equal(analysisNotice(code), '识别信息需要核对，请读取识别结果；若仍有异常，请联系支持。')
  assert.equal(analysisNotice('未选择控制计划，频次一致性缺少跨文件比对资料。'), '未选择控制计划，频次一致性缺少跨文件比对资料。')
})

const round = '00000000-0000-4000-8000-000000000001'

function finding(partial: Partial<Finding> & Pick<Finding, 'status'>): Finding {
  return {
    item_key: 'item-1',
    item: '零件号完整',
    classification: '基本信息',
    suggestions: '',
    ...partial,
  }
}

test('latestRules keeps versions that nothing superseded', () => {
  const v1 = {id: 'r1', title: '规则 v1', kind: 'engine', created_at: '2026-01-01'} satisfies Rule
  const v2 = {id: 'r2', title: '规则 v2', kind: 'engine', created_at: '2026-02-01', predecessor_id: 'r1'} satisfies Rule
  const other = {id: 'r3', title: '另一规则', kind: 'engine', created_at: '2026-02-02'} satisfies Rule
  assert.deepEqual(latestRules([]), [])
  assert.deepEqual(latestRules([v1]), [v1])
  assert.deepEqual(latestRules([v1, v2, other]).map(rule => rule.id), ['r2', 'r3'])
})

test('matchesFilter selects unreviewed, attention, and exact status', () => {
  const pass = finding({status: 'PASS'})
  const ng = finding({status: 'NG'})
  const err = finding({status: 'ERR'})
  const unable = finding({status: 'UNABLE'})
  const reviewed = finding({status: 'PASS', review: {judgment: '认可机器判断', rationale: '', next_step: '', version: 1}})
  assert.equal(matchesFilter(pass, 'unreviewed'), true)
  assert.equal(matchesFilter(reviewed, 'unreviewed'), false)
  assert.equal(matchesFilter(ng, 'attention'), true)
  assert.equal(matchesFilter(err, 'attention'), true)
  assert.equal(matchesFilter(unable, 'attention'), true)
  assert.equal(matchesFilter(pass, 'attention'), false)
  assert.equal(matchesFilter(pass, ''), true)
  assert.equal(matchesFilter(ng, ''), true)
  assert.equal(matchesFilter(pass, 'PASS'), true)
  assert.equal(matchesFilter(ng, 'PASS'), false)
})

test('findingShortTitle uses classification or a clause fallback', () => {
  assert.equal(findingShortTitle(finding({status: 'PASS'}), 3), '3 · 基本信息')
  assert.equal(findingShortTitle(finding({status: 'NG', classification: ''}), 1), '1 · 条款')
})

test('sourceLocation formats each semantic locator kind', () => {
  const file = {file_version_id: 'v', filename: 'a.xlsx', role: 'target' as const}
  const cell: SemanticSource = {...file, locator: {kind: 'xlsx_cell', sheet: '控制计划', cell: 'B12'}, excerpt: 'x'}
  const range: SemanticSource = {...file, locator: {kind: 'xlsx_range', sheet: '控制计划', start: 'A1', end: 'C3'}, excerpt: 'x'}
  const pdf: SemanticSource = {...file, filename: 'a.pdf', locator: {kind: 'pdf_block', page: 2, block: 4}, excerpt: 'x'}
  const paragraph: SemanticSource = {...file, filename: 'a.docx', locator: {kind: 'docx_paragraph', paragraph: 7}, excerpt: 'x'}
  const table: SemanticSource = {...file, filename: 'a.docx', locator: {kind: 'docx_cell', table: 1, row: 3, column: 2}, excerpt: 'x'}
  assert.equal(sourceLocation(cell), '控制计划!B12')
  assert.equal(sourceLocation(range), '控制计划!A1:C3')
  assert.equal(sourceLocation(pdf), '第 2 页 · 文本块 4')
  assert.equal(sourceLocation(paragraph), '段落 7')
  assert.equal(sourceLocation(table), '表格 1 · 行 3 · 列 2')
})

test('parseExcerptCells splits preview cells and marks formulas', () => {
  const cells = parseExcerptCells('A1: 零件号 | B1: =1+1 | AA10: later')
  assert.deepEqual(cells, [
    {address: 'A1', value: '零件号', formula: false},
    {address: 'B1', value: '=1+1', formula: true},
    {address: 'AA10', value: 'later', formula: false},
  ])
  assert.deepEqual(parseExcerptCells('unlabeled excerpt'), [
    {address: '', value: 'unlabeled excerpt', formula: false},
  ])
})

test('workspace storage key is round-scoped and parsing rejects unsafe input', () => {
  assert.equal(workspaceStorageKey(round), 'ppap-view:' + round)
  assert.equal(parseWorkspacePosition(null), null)
  assert.equal(parseWorkspacePosition(''), null)
  assert.equal(parseWorkspacePosition('{'), null)
  assert.equal(parseWorkspacePosition('[]'), null)
  assert.equal(parseWorkspacePosition('{"file":1}'), null)
  const saved = {
    file: 'version-1',
    finding: 'item-1',
    filter: 'PASS',
    view: 'review',
    panel: 'checks',
    scroll: 80,
  }
  assert.deepEqual(parseWorkspacePosition(JSON.stringify(saved)), saved)
})
